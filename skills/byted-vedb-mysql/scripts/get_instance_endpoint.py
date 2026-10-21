#!/usr/bin/env python3
# Copyright 2024 Bytedance Ltd. and/or its affiliates
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" specific language governing permissions and
# limitations under the License.

"""
火山引擎 VEDBM 实例连接地址查看脚本
查看指定实例的连接地址信息
"""

import argparse
import os
import sys
from typing import Dict, Optional

try:
    from dotenv import load_dotenv

    HAS_DOTENV = True
except ImportError:
    HAS_DOTENV = False

try:
    from volcenginesdkcore import Configuration, ApiClient
    from volcenginesdkvedbm import VEDBMApi, DescribeDBEndpointRequest
except ImportError as e:
    print(f"错误：未安装火山引擎 Python SDK 或导入失败: {e}")
    import traceback

    traceback.print_exc()
    print("请运行: pip install volcengine-python-sdk")
    sys.exit(1)


def get_instance_endpoint(
    region: str,
    instance_id: str,
    access_key_id: Optional[str] = None,
    secret_access_key: Optional[str] = None,
) -> Dict:
    """
    获取实例连接地址

    Args:
        region: 区域
        instance_id: 实例 ID
        access_key_id: 访问密钥 ID（可选，从环境变量读取）
        secret_access_key: 秘密访问密钥（可选，从环境变量读取）

    Returns:
        连接地址结果字典
    """
    # 配置 SDK
    configuration = Configuration()
    configuration.region = region
    if access_key_id:
        configuration.ak = access_key_id
    if secret_access_key:
        configuration.sk = secret_access_key

    # 创建 API 客户端
    api_client = ApiClient(configuration)
    api_instance = VEDBMApi(api_client)

    try:
        print(f"⏳ 正在查询实例 {instance_id} 的连接地址...")

        # 调用 API 获取连接地址
        request = DescribeDBEndpointRequest(instance_id=instance_id)
        response = api_instance.describe_db_endpoint(request)

        endpoints = []
        if hasattr(response, "endpoints") and response.endpoints:
            for endpoint in response.endpoints:
                endpoint_info = {
                    "endpoint_id": getattr(endpoint, "endpoint_id", None),
                    "endpoint_type": getattr(endpoint, "endpoint_type", None),
                    "addresses": [],
                }

                if hasattr(endpoint, "addresses") and endpoint.addresses:
                    for address in endpoint.addresses:
                        address_info = {
                            "domain": getattr(address, "domain", None),
                            "port": getattr(address, "port", None),
                            "ip_address": getattr(address, "ip_address", None),
                            "net_type": getattr(address, "net_type", None),
                        }
                        endpoint_info["addresses"].append(address_info)

                endpoints.append(endpoint_info)

        return {
            "success": True,
            "instance_id": instance_id,
            "region": region,
            "endpoints": endpoints,
            "total_count": len(endpoints),
        }

    except Exception as e:
        return {"success": False, "error": str(e), "instance_id": instance_id}


def print_result(result: Dict):
    """打印查询结果"""
    if result["success"]:
        print("\n" + "=" * 80)
        print("✅ 实例连接地址查询成功！")
        print("=" * 80)
        print(f"实例 ID: {result['instance_id']}")
        print(f"区域: {result['region']}")
        print(f"终端总数: {result['total_count']}")

        if result["endpoints"]:
            for i, endpoint in enumerate(result["endpoints"], 1):
                print(f"\n📍 终端 {i}:")
                print("-" * 80)
                print(f"  终端 ID: {endpoint['endpoint_id'] or '-'}")
                print(f"  终端类型: {endpoint['endpoint_type'] or '-'}")

                if endpoint["addresses"]:
                    print("\n  📡 连接地址:")
                    for j, address in enumerate(endpoint["addresses"], 1):
                        print(f"    地址 {j}:")
                        print(f"      域名: {address['domain'] or '-'}")
                        print(f"      端口: {address['port'] or '-'}")
                        print(f"      IP地址: {address['ip_address'] or '-'}")
                        print(f"      网络类型: {address['net_type'] or '-'}")
            print("-" * 80)
        else:
            print("\nℹ️  该实例暂无连接地址")

        print("=" * 80 + "\n")
    else:
        print("\n" + "=" * 80)
        print("❌ 实例连接地址查询失败！")
        print("=" * 80)
        print(f"错误信息: {result['error']}")
        if result.get("instance_id"):
            print(f"实例 ID: {result['instance_id']}")
        print("=" * 80 + "\n")


def main():
    # 尝试加载 .env 文件
    if HAS_DOTENV:
        # 尝试从多个位置加载 .env
        env_paths = [
            os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"),
            ".env",
        ]
        for env_path in env_paths:
            if os.path.exists(env_path):
                load_dotenv(env_path)
                print(f"✅ 已加载配置文件: {env_path}")
                break

    parser = argparse.ArgumentParser(description="查看火山引擎 VEDBM 实例连接地址")
    parser.add_argument(
        "--region",
        default=os.getenv("VEDBM_REGION", "cn-guangzhou"),
        help="区域（默认：cn-guangzhou，或环境变量 VEDBM_REGION）",
    )
    parser.add_argument("--instance-id", required=True, help="实例 ID（必填）")

    args = parser.parse_args()

    # 安全提示：AccessKey/SecretKey 仅从环境变量 / .env 读取，不接受命令行参数，
    # 避免凭据暴露在系统进程列表（ps / /proc/<pid>/cmdline）和 Shell 历史记录中。
    access_key_id = os.getenv("VOLCENGINE_ACCESS_KEY")
    secret_access_key = os.getenv("VOLCENGINE_SECRET_KEY")

    # 检查必填参数
    missing = []
    if not args.instance_id:
        missing.append("实例 ID（--instance-id）")
    if not access_key_id:
        missing.append("AccessKey ID（环境变量 VOLCENGINE_ACCESS_KEY）")
    if not secret_access_key:
        missing.append("Secret Access Key（环境变量 VOLCENGINE_SECRET_KEY）")

    if missing:
        print("❌ 缺少必要参数！")
        print("\n请提供以下信息：")
        for item in missing:
            print(f"  - {item}")
        print("\n⚠️  为避免凭据泄露，AK/SK 仅支持通过环境变量或 .env 文件提供，")
        print("    不要作为命令行参数传入（会暴露在进程列表和 Shell 历史中）。")
        print("\n方式 1：环境变量")
        print("  export VOLCENGINE_ACCESS_KEY=xxx")
        print("  export VOLCENGINE_SECRET_KEY=xxx")
        print("  python get_instance_endpoint.py --instance-id xxx")
        print("\n方式 2：.env 文件（cp .env.example .env 后填入真实信息）")
        sys.exit(1)

    result = get_instance_endpoint(
        region=args.region,
        instance_id=args.instance_id,
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
    )

    print_result(result)

    sys.exit(0 if result["success"] else 1)


if __name__ == "__main__":
    main()
