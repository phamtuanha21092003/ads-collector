import argparse
import logging

from app.main import main

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        force=True,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    main()
