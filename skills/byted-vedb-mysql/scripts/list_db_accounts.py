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
火山引擎 VEDBM 数据库账号列表查看脚本
查看指定实例中的数据库账号列表
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
    from volcenginesdkvedbm import VEDBMApi, DescribeDBAccountsRequest
except ImportError as e:
    print(f"错误：未安装火山引擎 Python SDK 或导入失败: {e}")
    import traceback

    traceback.print_exc()
    print("请运行: pip install volcengine-python-sdk")
    sys.exit(1)


def list_db_accounts(
    region: str,
    instance_id: str,
    access_key_id: Optional[str] = None,
    secret_access_key: Optional[str] = None,
) -> Dict:
    """
    获取数据库账号列表

    Args:
        region: 区域
        instance_id: 实例 ID
        access_key_id: 访问密钥 ID（可选，从环境变量读取）
        secret_access_key: 秘密访问密钥（可选，从环境变量读取）

    Returns:
        账号列表结果字典
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
        print(f"⏳ 正在查询实例 {instance_id} 的数据库账号列表...")

        # 调用 API 获取账号列表
        request = DescribeDBAccountsRequest(instance_id=instance_id)
        response = api_instance.describe_db_accounts(request)

        accounts = []
        if hasattr(response, "accounts") and response.accounts:
            for account in response.accounts:
                account_info = {
                    "account_name": getattr(account, "account_name", None),
                    "account_type": getattr(account, "account_type", None),
                    "account_status": getattr(account, "account_status", None),
                }
                accounts.append(account_info)

        return {
            "success": True,
            "instance_id": instance_id,
            "region": region,
            "accounts": accounts,
            "total_count": len(accounts),
        }

    except Exception as e:
        return {"success": False, "error": str(e), "instance_id": instance_id}


def print_result(result: Dict):
    """打印查询结果"""
    if result["success"]:
        print("\n" + "=" * 70)
        print("✅ 数据库账号列表查询成功！")
        print("=" * 70)
        print(f"实例 ID: {result['instance_id']}")
        print(f"区域: {result['region']}")
        print(f"账号总数: {result['total_count']}")

        if result["accounts"]:
            print("\n📋 账号列表：")
            print("-" * 70)
            print(f"{'账号名称':<20} {'账号类型':<15} {'账号状态':<15}")
            print("-" * 70)
            for account in result["accounts"]:
                account_name = account["account_name"] or "-"
                account_type = account["account_type"] or "-"
                account_status = account["account_status"] or "-"
                print(f"{account_name:<20} {account_type:<15} {account_status:<15}")
            print("-" * 70)
        else:
            print("\nℹ️  该实例中暂无数据库账号")

        print("=" * 70 + "\n")
    else:
        print("\n" + "=" * 70)
        print("❌ 数据库账号列表查询失败！")
        print("=" * 70)
        print(f"错误信息: {result['error']}")
        if result.get("instance_id"):
            print(f"实例 ID: {result['instance_id']}")
        print("=" * 70 + "\n")


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

    parser = argparse.ArgumentParser(
        description="查看火山引擎 VEDBM 实例数据库账号列表"
    )
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
        print("  python list_db_accounts.py --instance-id xxx")
        print("\n方式 2：.env 文件（cp .env.example .env 后填入真实信息）")
        sys.exit(1)

    result = list_db_accounts(
        region=args.region,
        instance_id=args.instance_id,
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
    )

    print_result(result)

    sys.exit(0 if result["success"] else 1)


if __name__ == "__main__":
    main()
