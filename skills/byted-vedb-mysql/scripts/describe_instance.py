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
火山引擎 VEDBM 实例详情查询脚本
查看指定实例的完整详细信息
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


def describe_instance(
    region: str,
    instance_id: str,
    access_key_id: Optional[str] = None,
    secret_access_key: Optional[str] = None,
    show_full: bool = False,
) -> Dict:
    """
    获取实例详细信息

    Args:
        region: 区域
        instance_id: 实例 ID
        access_key_id: 访问密钥 ID（可选，从环境变量读取）
        secret_access_key: 秘密访问密钥（可选，从环境变量读取）
        show_full: 是否显示完整属性列表

    Returns:
        实例详细信息
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
        print(f"⏳ 正在查询实例 {instance_id} 的详细信息...")

        # 调用 API 获取实例详情
        request = DescribeDBInstancesRequest(instance_id=instance_id)
        response = api_instance.describe_db_instances(request)

        if not response.instances or len(response.instances) == 0:
            return {"success": False, "error": f"未找到实例 {instance_id}"}

        instance = response.instances[0]

        # 打印完整属性列表（如果需要）
        if show_full:
            print("\n" + "=" * 80)
            print("📋 实例完整属性列表：")
            print("=" * 80)
            for attr in dir(instance):
                if not attr.startswith("_"):
                    try:
                        value = getattr(instance, attr)
                        if not callable(value):
                            print(f"  {attr}: {value}")
                    except Exception as e:
                        print(f"  {attr}: <无法读取 - {e}>")
            print("=" * 80 + "\n")

        # 从 nodes 数组中提取信息
        node_spec = None
        node_number = 0
        nodes_info = []
        v_cpu = None
        memory = None

        if hasattr(instance, "nodes") and instance.nodes:
            node_number = len(instance.nodes)
            for i, node in enumerate(instance.nodes):
                node_info = {
                    "node_id": getattr(node, "node_id", None),
                    "node_type": getattr(node, "node_type", None),
                    "node_spec": getattr(node, "node_spec", None),
                    "v_cpu": getattr(node, "v_cpu", None),
                    "memory": getattr(node, "memory", None),
                    "zone_id": getattr(node, "zone_id", None),
                    "failover_priority": getattr(node, "failover_priority", None),
                }
                nodes_info.append(node_info)

                # 取第一个节点的规格作为实例规格
                if i == 0:
                    node_spec = node_info["node_spec"]
                    v_cpu = node_info["v_cpu"]
                    memory = node_info["memory"]

        # 从 charge_detail 中提取计费信息
        charge_type = None
        charge_status = None
        if hasattr(instance, "charge_detail") and instance.charge_detail:
            charge_type = getattr(instance.charge_detail, "charge_type", None)
            charge_status = getattr(instance.charge_detail, "charge_status", None)

        # 构建返回结果
        return {
            "success": True,
            "instance_id": getattr(instance, "instance_id", None),
            "instance_name": getattr(instance, "instance_name", None),
            "instance_status": getattr(instance, "instance_status", None),
            "node_spec": node_spec,
            "v_cpu": v_cpu,
            "memory": memory,
            "node_number": node_number,
            "region": getattr(instance, "region_id", None),
            "zone_id": getattr(instance, "zone_ids", None),
            "create_time": getattr(instance, "create_time", None),
            "charge_type": charge_type,
            "charge_status": charge_status,
            "db_engine_version": getattr(instance, "db_engine_version", None),
            "db_revision_version": getattr(instance, "db_revision_version", None),
            "spec_family": getattr(instance, "spec_family", None),
            "storage_used_gib": getattr(instance, "storage_used_gi_b", None),
            "storage_charge_type": getattr(instance, "storage_charge_type", None),
            "vpc_id": getattr(instance, "vpc_id", None),
            "subnet_id": getattr(instance, "subnet_id", None),
            "project_name": getattr(instance, "project_name", None),
            "deletion_protection": getattr(instance, "deletion_protection", None),
            "time_zone": getattr(instance, "time_zone", None),
            "nodes": nodes_info,
            "full_info": instance,
        }

    except Exception as e:
        import traceback

        traceback.print_exc()
        return {"success": False, "error": str(e)}


def print_result(result: Dict):
    """打印查询结果"""
    if result["success"]:
        print("\n" + "=" * 100)
        print("✅ VEDBM 实例详情查询成功！")
        print("=" * 100)

        # 基本信息
        print("\n📋 基本信息：")
        print("-" * 100)
        print(f"  实例 ID:        {result['instance_id']}")
        print(f"  实例名称:      {result['instance_name']}")
        print(f"  实例状态:      {result['instance_status']}")
        print(f"  创建时间:      {result['create_time']}")
        print(f"  项目名称:      {result['project_name']}")

        # 规格信息
        print("\n💻 规格信息：")
        print("-" * 100)
        print(f"  节点规格:      {result['node_spec']}")
        if result.get("v_cpu") and result.get("memory"):
            print(f"  配置:          {result['v_cpu']}核 {result['memory']}GB")
        print(f"  节点数量:      {result['node_number']}")
        print(f"  规格系列:      {result['spec_family']}")

        # 存储信息
        print("\n💾 存储信息：")
        print("-" * 100)
        print(
            f"  已用存储:      {result['storage_used_gib']} GiB"
            if result.get("storage_used_gib")
            else "  已用存储:      -"
        )
        print(f"  存储计费类型:  {result['storage_charge_type']}")

        # 网络信息
        print("\n🌐 网络信息：")
        print("-" * 100)
        print(f"  区域:          {result['region']}")
        print(f"  可用区:        {result['zone_id']}")
        print(f"  VPC ID:        {result['vpc_id']}")
        print(f"  子网 ID:       {result['subnet_id']}")

        # 数据库信息
        print("\n🗄️  数据库信息：")
        print("-" * 100)
        print(f"  数据库版本:    {result['db_engine_version']}")
        print(f"  内核版本:      {result['db_revision_version']}")
        print(f"  时区:          {result['time_zone']}")

        # 计费信息
        print("\n💰 计费信息：")
        print("-" * 100)
        print(f"  计费类型:      {result['charge_type']}")
        print(f"  计费状态:      {result['charge_status']}")
        print(f"  删除保护:      {result['deletion_protection']}")

        # 节点详情
        if result.get("nodes"):
            print("\n🔧 节点详情：")
            print("-" * 100)
            print(f"  {'节点 ID':<35} {'类型':<12} {'规格':<25} {'可用区'}")
            print("-" * 100)
            for node in result["nodes"]:
                node_id = node.get("node_id", "-")
                node_type = node.get("node_type", "-")
                node_spec = node.get("node_spec", "-")
                zone_id = node.get("zone_id", "-")
                print(f"  {node_id:<35} {node_type:<12} {node_spec:<25} {zone_id}")

        print("=" * 100 + "\n")
    else:
        print("\n" + "=" * 100)
        print("❌ VEDBM 实例详情查询失败！")
        print("=" * 100)
        print(f"错误信息: {result['error']}")
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

    parser = argparse.ArgumentParser(description="查看火山引擎 VEDBM 实例详情")
    parser.add_argument(
        "--region",
        default=os.getenv("VEDBM_REGION", "cn-guangzhou"),
        help="区域（默认：cn-guangzhou，或环境变量 VEDBM_REGION）",
    )
    parser.add_argument("--instance-id", required=True, help="实例 ID（必填）")
    parser.add_argument(
        "--full", action="store_true", help="显示完整属性列表（调试用）"
    )

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
        print("\n⚠️  AK/SK 仅支持通过环境变量或 .env 文件提供（不要用命令行参数，会泄露）。")
        print("\n使用示例：")
        print("  export VOLCENGINE_ACCESS_KEY=xxx")
        print("  export VOLCENGINE_SECRET_KEY=xxx")
        print("  python describe_instance.py --instance-id vedbm-xxx123456")
        sys.exit(1)

    result = describe_instance(
        region=args.region,
        instance_id=args.instance_id,
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
        show_full=args.full,
    )

    print_result(result)

    sys.exit(0 if result["success"] else 1)


if __name__ == "__main__":
    main()
