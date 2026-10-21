"""Task Scheduler entry point. Never enables or registers a Windows task."""
import argparse
from datetime import datetime
import os
from pathlib import Path

from amazon_importer import scheduled_runner

BASE_DIR = Path(__file__).resolve().parent
LOG_FILE = BASE_DIR / 'scheduled_crawl.log'
API_URL = os.getenv('AMAZON_CRAWL_API_URL', 'http://127.0.0.1:6471/api/amazon-day-data/crawl')
TIMEOUT_SECONDS = int(os.getenv('AMAZON_CRAWL_TIMEOUT_SECONDS', '21600'))


def write_log(message):
    with LOG_FILE.open('a', encoding='utf-8') as file:
        file.write(f'[{datetime.now():%Y-%m-%d %H:%M:%S}] {message}\n')


def main(resume_auth=False):
    try:
        with scheduled_runner.acquire_locks():
            return scheduled_runner.run(write_log, API_URL, TIMEOUT_SECONDS, resume_auth=resume_auth)
    except Exception as exc:
        write_log(f'failed error={exc}')
        return 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--resume-auth', action='store_true', help='只补拉当天待认证站点，跳过已完整商品')
    raise SystemExit(main(parser.parse_args().resume_auth))
