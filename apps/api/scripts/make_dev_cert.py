#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""生成**本地开发用**自签 TLS 证书（合规 6.2「HTTPS 加密传输」落地）

仅用于本地/内网测试与演示：自签证书不被浏览器/系统信任，生产环境请使用受信任
CA 签发的证书，或把服务置于 HTTPS 反向代理之后（见 docs/COMPLIANCE.md）。

用法
----
  python apps/api/scripts/make_dev_cert.py --out apps/api/data/certs
  python apps/api/scripts/make_dev_cert.py --host 192.168.1.9 --days 365

依赖：cryptography（项目已装）。生成文件：``dev_key.pem`` / ``dev_cert.pem``。
"""
from __future__ import annotations

import argparse
import datetime
import ipaddress
import sys
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser(description="生成本地自签 TLS 证书（开发用）")
    ap.add_argument("--out", default="apps/api/data/certs", help="输出目录")
    ap.add_argument("--host", action="append", default=[],
                    help="额外主机名/IP（可多次）")
    ap.add_argument("--days", type=int, default=365, help="有效期天数")
    args = ap.parse_args()

    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID
    except Exception as e:                                    # noqa: BLE001
        print(f"[x] 需要 cryptography：pip install cryptography（{e}）")
        return 2

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    key_path = out / "dev_key.pem"
    cert_path = out / "dev_cert.pem"

    hosts = ["localhost", "127.0.0.1", "::1"] + list(args.host)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, "PokéLab AI (dev self-signed)"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "PokéLab AI local dev"),
    ])
    alt = []
    for h in hosts:
        try:
            alt.append(x509.IPAddress(ipaddress.ip_address(h)))
        except ValueError:
            alt.append(x509.DNSName(h))
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder()
            .subject_name(subject).issuer_name(issuer)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(minutes=5))
            .not_valid_after(now + datetime.timedelta(days=args.days))
            .add_extension(x509.SubjectAlternativeName(alt), critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None),
                           critical=True)
            .sign(key, hashes.SHA256()))

    key_path.write_bytes(key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption()))
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    try:                                                      # 尽力限制权限
        import os
        import stat
        os.chmod(key_path, stat.S_IRUSR | stat.S_IWUSR)
    except Exception:                                         # noqa: BLE001
        pass

    print(f"[ok] 私钥 {key_path}")
    print(f"[ok] 证书 {cert_path}（SAN: {', '.join(hosts)}，{args.days} 天）")
    print("\n以 HTTPS 启动：")
    print(f"  python -m uvicorn apps.api.app.main:app --port 8443 \\\n"
          f"    --ssl-keyfile {key_path} --ssl-certfile {cert_path}")
    print("\n[注意] 自签证书不被浏览器信任，仅用于本地/内网测试；"
          "生产请用受信任 CA 或反向代理（docs/COMPLIANCE.md）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
