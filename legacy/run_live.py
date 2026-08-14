"""
Start the price collector AND the news engine together in the background,
so the dashboard always has fresh data.

How to use:
  Terminal 1:  python run_live.py
  Terminal 2:  streamlit run dashboard.py
"""

import threading
import time

import collector
import config
import database
import news


def run_prices() -> None:
    conn = database.connect()
    database.init_db(conn)
    collector.live_loop(conn)


def run_news() -> None:
    news.live_loop()


def main() -> None:
    print("Starting live data loops...")
    threading.Thread(target=run_prices, daemon=True).start()
    threading.Thread(target=run_news, daemon=True).start()

    print("Press Ctrl+C to stop.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
