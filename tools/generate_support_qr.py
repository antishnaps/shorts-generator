"""Generate the public Ethereum receiving QR; no runtime QR dependency needed.

Development-only dependency: pip install 'qrcode[pil]>=8,<9'.
Run after changing payment_methods.json; tests verify QR/address equality.
"""
import json
from pathlib import Path

import qrcode
from PIL.PngImagePlugin import PngInfo

ROOT = Path(__file__).resolve().parents[1]


def main():
    assets = ROOT / "assets/support"
    data = json.loads((assets / "payment_methods.json").read_text(encoding="utf-8"))
    address = data["ethereum_address"]
    if data["ethereum_chain_id"] != 1:
        raise ValueError("This QR is for Ethereum mainnet only")
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_H, box_size=8, border=4)
    qr.add_data(address)
    qr.make(fit=True)
    metadata = PngInfo()
    metadata.add_text("qr_payload", address)
    qr.make_image(fill_color="black", back_color="white").save(assets / "ethereum_address.png", pnginfo=metadata)
    print("SUPPORT_QR_OK")


if __name__ == "__main__":
    main()
