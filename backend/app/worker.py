import asyncio
import logging

from app.account_deletion import process_due_account_deletions
from app.auth import init_firebase


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s")
    init_firebase()
    await process_due_account_deletions()


if __name__ == "__main__":
    asyncio.run(main())
