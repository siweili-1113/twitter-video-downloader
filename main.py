"""
Twitter/X Media Downloader
A high-performance async media downloader for Twitter/X tweets.
"""
import re
import os
import json
import httpx
import asyncio
import sys
import urllib.parse
from datetime import datetime
import time
from tqdm import tqdm


def _quote_url(url):
    """Escape braces in URL to avoid httpx parsing issues."""
    return url.replace('{', '%7B').replace('}', '%7D')


_DEFAULT_BEARER = ('AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs'
                   '%3D1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA')


def get_tweet_id_safe(url: str):
    match = re.search(r'/status/(\d+)', url)
    return match.group(1) if match else None


async def download_file(client, url, filename, _settings):
    count = 0
    while True:
        try:
            async with client.stream('GET', _quote_url(url), timeout=(3.05, 16)) as response:
                if response.status_code == 404:
                    raise Exception('404')

                total = int(response.headers.get('content-length', 0))
                filename_part = filename + '.part'

                with tqdm(total=total, unit='iB', unit_scale=True,
                          desc=os.path.basename(filename)) as pbar:
                    os.makedirs(os.path.dirname(filename), exist_ok=True)
                    with open(filename_part, 'wb') as f:
                        async for chunk in response.aiter_bytes(chunk_size=1024):
                            f.write(chunk)
                            pbar.update(len(chunk))

                if os.path.exists(filename):
                    os.remove(filename)
                os.rename(filename_part, filename)

                print(f"Downloaded: {filename}")
                break
        except Exception:
            count += 1
            if count >= 50:
                print(f'{filename} ==> Failed after {count} retries, skipped.')
                print(url)
                break
            print(f'{filename} ==> Retry #{count}')
            print(url)
            await asyncio.sleep(1)


async def _fetch_tweet_info(url, tweet_id, headers, settings):
    """
    Fetch tweet media info from Twitter internal API.
    Rate limit (429) waits do not consume retry quota.
    Returns (user_name, tweet_time, media_list) or (None, None, None).
    """
    api_url = f'https://api.twitter.com/2/timeline/conversation/{tweet_id}.json?tweet_mode=extended'
    user_name = re.search(r'(?:twitter\.com|x\.com)/([^/]+)/', url).group(1)

    max_retries = 3
    attempt = 0
    while attempt < max_retries:
        try:
            client_kwargs = {}
            if settings.get('proxy'):
                client_kwargs['proxy'] = settings['proxy']

            async with httpx.AsyncClient(**client_kwargs) as client:
                response = await client.get(_quote_url(api_url), headers=headers)

                if response.status_code == 429:
                    retry_after = response.headers.get('retry-after', '60')
                    try:
                        wait_s = int(retry_after)
                    except ValueError:
                        wait_s = 60
                    print(f"Rate limited (HTTP 429), waiting {wait_s}s (retry quota preserved)...")
                    await asyncio.sleep(wait_s)
                    continue

                if response.status_code in (503, 502):
                    attempt += 1
                    print(f"API server error: HTTP {response.status_code} ({attempt}/{max_retries})")
                    if attempt < max_retries:
                        await asyncio.sleep(2 ** attempt)
                    continue

                if response.status_code != 200:
                    attempt += 1
                    print(f"API request failed: HTTP {response.status_code} ({attempt}/{max_retries})")
                    if attempt < max_retries:
                        await asyncio.sleep(2 ** attempt)
                    continue

                try:
                    data = response.json()
                except json.JSONDecodeError:
                    attempt += 1
                    print(f"API JSON decode failed ({attempt}/{max_retries})")
                    if attempt < max_retries:
                        await asyncio.sleep(2 ** attempt)
                    continue

                tweets = data.get('globalObjects', {}).get('tweets', {})
                if tweet_id not in tweets:
                    print(f"Tweet {tweet_id} not found in response (deleted or private).")
                    return None, None, None

                tweet_data = tweets[tweet_id]
                created_time = datetime.strptime(
                    tweet_data['created_at'], '%a %b %d %H:%M:%S +0000 %Y')
                tweet_time = created_time.strftime("%Y-%m-%d %H-%M")

                media_list = []
                if 'extended_entities' in tweet_data and 'media' in tweet_data['extended_entities']:
                    media_list = tweet_data['extended_entities']['media']
                elif 'entities' in tweet_data and 'media' in tweet_data['entities']:
                    media_list = tweet_data['entities']['media']

                return user_name, tweet_time, media_list

        except Exception as e:
            attempt += 1
            print(f"API request exception ({attempt}/{max_retries}): {e}")
            if attempt < max_retries:
                await asyncio.sleep(2 ** attempt)

    return None, None, None


def _split_urls(lines):
    """Split multi‑link lines like '...https://a...https://b...' into individual links."""
    results = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        parts = re.split(r'(?=https?://)', line)
        for part in parts:
            part = part.strip()
            if part:
                results.append(part)
    return results


async def process_urls(urls, save_path, settings):
    url_lines = _split_urls(urls)
    unique_urls = list(dict.fromkeys([u.strip() for u in url_lines if u.strip()]))
    print(f"Total links: {len(url_lines)}, after dedup: {len(unique_urls)}")

    api_sem = asyncio.Semaphore(settings.get('max_api_concurrent', 3))
    dl_sem = asyncio.Semaphore(settings.get('max_concurrent_requests', 8))
    api_delay = settings.get('api_delay', 0.5)
    print(f"API concurrency: {settings.get('max_api_concurrent', 3)} | "
          f"Download concurrency: {settings.get('max_concurrent_requests', 8)} | "
          f"API interval: {api_delay}s")

    headers = _build_headers(settings)
    if headers is None:
        print("Fatal: Failed to build request headers, aborting.")
        return

    with tqdm(total=len(unique_urls), desc="Overall Progress") as pbar:
        tasks = []
        for url in unique_urls:
            task = asyncio.create_task(
                _process_one_url(url, save_path, settings, headers, pbar,
                                 api_sem, dl_sem, api_delay)
            )
            tasks.append(task)
        await asyncio.gather(*tasks, return_exceptions=True)

    print("All tasks completed!")


async def _process_one_url(url, save_path, settings, headers, pbar,
                           api_sem, dl_sem, api_delay):
    try:
        tweet_id = get_tweet_id_safe(url)
        if tweet_id is None:
            print(f"Invalid tweet link: {url}")
            pbar.update(1)
            return

        async with api_sem:
            await asyncio.sleep(api_delay)
            user_name, tweet_time, media_list = await _fetch_tweet_info(
                url, tweet_id, headers, settings)

        if not media_list:
            if user_name is None and tweet_time is None:
                print(f"API fetch failed: {url}")
            else:
                print("No media content in this tweet.")
                _write_no_media(url)
            pbar.update(1)
            return

        base_name = f"{user_name}_{tweet_id}_{tweet_time}"
        download_tasks = []

        client_kwargs = {}
        if settings.get('proxy'):
            client_kwargs['proxy'] = settings['proxy']

        async with httpx.AsyncClient(**client_kwargs) as client:
            for i, media in enumerate(media_list):
                if media['type'] == 'video' and settings.get('has_video', True):
                    variants = media.get('video_info', {}).get('variants', [])
                    if not variants:
                        video_url = media.get('media_url_https', '')
                    else:
                        try:
                            video_url = max(
                                (v for v in variants if 'bitrate' in v),
                                key=lambda x: x['bitrate']
                            )['url']
                        except ValueError:
                            video_url = variants[0].get('url', '') if variants else ''
                    if not video_url:
                        continue
                    filename = f"{save_path}/{base_name}_vid_{i}.mp4"
                    if os.path.exists(filename):
                        print(f"File exists, skip: {filename}")
                        continue
                    download_tasks.append(download_file(client, video_url, filename, settings))

                elif media['type'] == 'photo':
                    photo_url = media.get('media_url_https', '')
                    if not photo_url:
                        continue
                    parsed_url = urllib.parse.urlparse(photo_url)
                    query_params = urllib.parse.parse_qs(parsed_url.query)
                    ext = None
                    if 'format' in query_params:
                        ext = query_params['format'][0].lower()
                    if not ext:
                        ext = photo_url.split('.')[-1].lower()
                    if ext == 'jfif':
                        ext = 'jpg'
                    if settings.get('image_format') == 'orig':
                        photo_url += '?name=orig'
                    else:
                        ext = settings.get('image_format', ext)
                        photo_url += f'?format={ext}&name=4096x4096'
                    filename = f"{save_path}/{base_name}_img_{i}.{ext}"
                    if os.path.exists(filename):
                        print(f"File exists, skip: {filename}")
                        continue
                    download_tasks.append(download_file(client, photo_url, filename, settings))

            if download_tasks:
                async with dl_sem:
                    await asyncio.gather(*download_tasks, return_exceptions=True)
                print(f"Download completed: {url}")
            else:
                print(f"All files already exist, skip: {url}")

    except Exception as e:
        print(f"Processing error [{url}]: {e}")

    finally:
        pbar.update(1)


def _write_no_media(url: str):
    """Log tweets that have no media content."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    no_media_path = os.path.join(script_dir, "no_media.txt")
    try:
        with open(no_media_path, 'a', encoding='utf8') as f:
            f.write(url + '\n')
    except Exception as e:
        print(f"Failed to write no_media.txt: {e}")


def _build_headers(settings):
    try:
        ct = str(int(time.time() * 1000))
    except Exception:
        ct = "1234567890"

    bearer_token = settings.get('bearer_token') or _DEFAULT_BEARER

    # Cookie: env var takes priority over settings.json
    cookie = os.environ.get('TWITTER_COOKIE', settings.get('cookie', ''))

    headers = {
        'user-agent': ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                       'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36'),
        'authorization': f'Bearer {bearer_token}',
        'cookie': cookie,
        'x-client-transaction-id': ct,
        'x-twitter-active-user': 'yes',
        'x-twitter-client-language': 'en'
    }

    re_token = 'ct0=(.*?);'
    try:
        headers['x-csrf-token'] = re.findall(re_token, headers['cookie'])[0]
    except IndexError:
        print("Error: Unable to extract ct0 token from cookie. Check cookie format.")
        return None

    return headers


if __name__ == '__main__':
    script_dir = os.path.dirname(os.path.abspath(__file__))
    settings_path = os.path.join(script_dir, 'config.json')

    default_settings = {
        "save_path": "",
        "url_file": "links.txt",
        "cookie": "",
        "bearer_token": "",
        "image_format": "orig",
        "has_video": True,
        "log_output": False,
        "proxy": None,
        "max_concurrent_requests": 8,
        "max_api_concurrent": 3,
        "api_delay": 0.5
    }

    if not os.path.exists(settings_path):
        with open(settings_path, 'w', encoding='utf8') as f:
            json.dump(default_settings, f, indent=4)
        print(f"Default config created: {settings_path}")
        print("Please fill in the required fields (cookie) and run again.")
        sys.exit(0)

    with open(settings_path, 'r', encoding='utf8') as f:
        settings = json.load(f)

    for key, value in default_settings.items():
        if key not in settings:
            settings[key] = value

    if not settings['save_path']:
        settings['save_path'] = os.getcwd()

    url_file = os.path.join(script_dir, settings['url_file'])
    with open(url_file, 'r', encoding='utf8') as f:
        urls = f.readlines()

    asyncio.run(process_urls(urls, settings['save_path'], settings))