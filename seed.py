# -*- coding: utf-8 -*-
import asyncio

from db import add_key, init_db

DEMO_KEYS = {
    "ngotran_1": ["NGOTRAN-1D-DEMO-001", "NGOTRAN-1D-DEMO-002"],
    "ngotran_7": ["NGOTRAN-7D-DEMO-001", "NGOTRAN-7D-DEMO-002"],
    "ngotran_30": ["NGOTRAN-30D-DEMO-001", "NGOTRAN-30D-DEMO-002"],
    "strickbr_1": ["STRICKBR-1D-DEMO-001", "STRICKBR-1D-DEMO-002"],
    "strickbr_7": ["STRICKBR-7D-DEMO-001", "STRICKBR-7D-DEMO-002"],
    "strickbr_30": ["STRICKBR-30D-DEMO-001", "STRICKBR-30D-DEMO-002"],
}


async def main() -> None:
    await init_db()
    count = 0
    for product_id, keys in DEMO_KEYS.items():
        for key in keys:
            if await add_key(product_id, key):
                count += 1
    print(f"Đã thêm {count} key demo.")


if __name__ == "__main__":
    asyncio.run(main())
