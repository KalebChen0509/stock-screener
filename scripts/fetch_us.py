"""下載 SEC 大量檔，建立美股股票池與季度財報。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from screener import data_us  # noqa: E402


def main():
    uni = data_us.build_universe(download=True)
    data_us.build_fundamentals(uni, download=True)


if __name__ == "__main__":
    main()
