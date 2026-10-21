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
火山引擎 VEDBM 实例列表查看脚本
查看指定地域下的所有 VEDBM 实例
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
    from volcenginesdkvedbm import VEDBMApi, DescribeDBInstancesRequest
except ImportError as e:
    print(f"错误：未安装火山引擎 Python SDK 或导入失败: {e}")
    import traceback

    traceback.print_exc()
    print("请运行: pip install volcengine-python-sdk")
    sys.exit(1)


def list_instances(
    region: str,
    access_key_id: Optional[str] = None,
    secret_access_key: Optional[str] = None,
) -> Dict:
    """
    获取实例列表

    Args:
        region: 区域
        access_key_id: 访问密钥 ID（可选，从环境变量读取）
        secret_access_key: 秘密访问密钥（可选，从环境变量读取）

    Returns:
        实例列表结果字典
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
        print(f"⏳ 正在查询 {region} 区域下的 VEDBM 实例列表...")

        # 调用 API 获取实例列表
        request = DescribeDBInstancesRequest()
        response = api_instance.describe_db_instances(request)

        instances = []
        if hasattr(response, "instances") and response.instances:
            for instance in response.instances:
                # 从 nodes 数组中获取节点规格和可用区
                node_spec = None
                zone_id = None
                if hasattr(instance, "nodes") and instance.nodes:
                    for node in instance.nodes:
                        if hasattr(node, "node_spec") and node.node_spec:
                            node_spec = node.node_spec
                        if hasattr(node, "zone_id") and node.zone_id:
                            zone_id = node.zone_id
                        if node_spec and zone_id:
                            break

                instance_info = {
                    "instance_id": getattr(instance, "instance_id", None),
                    "instance_name": getattr(instance, "instance_name", None),
                    "instance_status": getattr(instance, "instance_status", None),
                    "node_spec": node_spec,
                    "region": getattr(instance, "region", None),
                    "zone_id": zone_id,
                    "create_time": getattr(instance, "create_time", None),
                }
                instances.append(instance_info)

        return {
            "success": True,
            "region": region,
            "instances": instances,
            "total_count": len(instances),
        }

    except Exception as e:
        return {"success": False, "error": str(e), "region": region}


def print_result(result: Dict):
    """打印查询结果"""
    if result["success"]:
        print("\n" + "=" * 100)
        print("✅ VEDBM 实例列表查询成功！")
        print("=" * 100)
        print(f"区域: {result['region']}")
        print(f"实例总数: {result['total_count']}")

        if result["instances"]:
            print("\n📋 实例列表：")
            print("-" * 100)
            print(
                f"{'实例 ID':<25} {'实例名称':<25} {'状态':<12} {'规格':<20} {'可用区':<15}"
            )
            print("-" * 100)
            for instance in result["instances"]:
                instance_id = instance["instance_id"] or "-"
                instance_name = instance["instance_name"] or "-"
                instance_status = instance["instance_status"] or "-"
                node_spec = instance["node_spec"] or "-"
                zone_id = instance["zone_id"] or "-"
                print(
                    f"{instance_id:<25} {instance_name:<25} {instance_status:<12} {node_spec:<20} {zone_id:<15}"
                )
            print("-" * 100)
        else:
            print("\nℹ️  该区域下暂无 VEDBM 实例")

        print("=" * 100 + "\n")
    else:
        print("\n" + "=" * 100)
        print("❌ VEDBM 实例列表查询失败！")
        print("=" * 100)
        print(f"错误信息: {result['error']}")
        if result.get("region"):
            print(f"区域: {result['region']}")
        print("=" * 100 + "\n")


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

    parser = argparse.ArgumentParser(description="查看火山引擎 VEDBM 实例列表")
    parser.add_argument(
        "--region",
        default=os.getenv("VEDBM_REGION", "cn-guangzhou"),
        help="区域（默认：cn-guangzhou，或环境变量 VEDBM_REGION）",
    )

    args = parser.parse_args()

    # 安全提示：AccessKey/SecretKey 仅从环境变量 / .env 读取，不接受命令行参数，
    # 避免凭据暴露在系统进程列表（ps / /proc/<pid>/cmdline）和 Shell 历史记录中。
    access_key_id = os.getenv("VOLCENGINE_ACCESS_KEY")
    secret_access_key = os.getenv("VOLCENGINE_SECRET_KEY")

    # 检查必填参数
    missing = []
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
        print("  python list_instances.py")
        print("\n方式 2：.env 文件（cp .env.example .env 后填入真实信息）")
        print("\n方式 3：非敏感参数用命令行，AK/SK 仍用环境变量")
        print("  python list_instances.py --region cn-beijing")
        sys.exit(1)

    result = list_instances(
        region=args.region,
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
    )

    print_result(result)

    sys.exit(0 if result["success"] else 1)


if __name__ == "__main__":
    main()
