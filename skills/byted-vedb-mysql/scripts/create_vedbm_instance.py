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
火山引擎 VEDBM 实例创建脚本
创建 4C16G 规格的 VEDBM 实例，用户名 root，密码随机生成
"""

import argparse
import os
import random
import string
import sys
import time
from typing import Dict, Optional

try:
    from dotenv import load_dotenv

    HAS_DOTENV = True
except ImportError:
    HAS_DOTENV = False

try:
    from volcenginesdkcore import Configuration, ApiClient
    from volcenginesdkvedbm import (
        VEDBMApi,
        CreateDBInstanceRequest,
        DescribeDBInstancesRequest,
        DescribeDBEndpointRequest,
    )
except ImportError as e:
    print(f"错误：未安装火山引擎 Python SDK 或导入失败: {e}")
    import traceback

    traceback.print_exc()
    print("请运行: pip install volcengine-python-sdk")
    sys.exit(1)


def generate_password(length: int = 16) -> str:
    """
    生成随机密码
    包含大小写字母、数字和特殊字符
    """
    characters = string.ascii_letters + string.digits + "!@#$%^&*"
    password = []

    # 确保至少包含一个大写字母、小写字母、数字和特殊字符
    password.append(random.choice(string.ascii_uppercase))
    password.append(random.choice(string.ascii_lowercase))
    password.append(random.choice(string.digits))
    password.append(random.choice("!@#$%^&*"))

    # 填充剩余字符
    for _ in range(length - 4):
        password.append(random.choice(characters))

    # 打乱顺序
    random.shuffle(password)
    return "".join(password)


def generate_instance_name() -> str:
    """生成随机实例名称"""
    timestamp = int(time.time())
    return f"vedbm-instance-{timestamp}"


def get_instance_status(api_instance: VEDBMApi, instance_id: str) -> Optional[str]:
    """
    获取实例状态

    Args:
        api_instance: VEDBM API 实例
        instance_id: 实例 ID

    Returns:
        实例状态
    """
    try:
        request = DescribeDBInstancesRequest(instance_id=instance_id)
        response = api_instance.describe_db_instances(request)

        if not response.instances or len(response.instances) == 0:
            return None

        instance = response.instances[0]
        return (
            instance.instance_status if hasattr(instance, "instance_status") else None
        )

    except Exception as e:
        print(f"⚠️  查询实例状态失败: {e}")
        return None


def get_instance_endpoint(api_instance: VEDBMApi, instance_id: str) -> Optional[Dict]:
    """
    获取实例连接地址

    Args:
        api_instance: VEDBM API 实例
        instance_id: 实例 ID

    Returns:
        连接信息字典
    """
    try:
        request = DescribeDBEndpointRequest(instance_id=instance_id)
        response = api_instance.describe_db_endpoint(request)

        if not response.endpoints or len(response.endpoints) == 0:
            return None

        # 优先找主节点终端
        primary_endpoint = None
        default_endpoint = None

        for endpoint in response.endpoints:
            if hasattr(endpoint, "endpoint_type"):
                if endpoint.endpoint_type == "Primary":
                    primary_endpoint = endpoint
                elif endpoint.endpoint_type == "Cluster":
                    default_endpoint = endpoint

        # 使用主节点终端，如果没有则用默认终端
        selected_endpoint = (
            primary_endpoint or default_endpoint or response.endpoints[0]
        )

        if hasattr(selected_endpoint, "addresses") and selected_endpoint.addresses:
            for address in selected_endpoint.addresses:
                if hasattr(address, "domain") and address.domain:
                    return {
                        "connection_address": address.domain,
                        "port": int(address.port)
                        if hasattr(address, "port") and address.port
                        else 3306,
                    }

        return None

    except Exception as e:
        print(f"⚠️  查询连接地址失败: {e}")
        return None


def wait_for_instance_ready(
    api_instance: VEDBMApi,
    instance_id: str,
    timeout: int = 1200,
    poll_interval: int = 30,
) -> bool:
    """
    等待实例就绪

    Args:
        api_instance: VEDBM API 实例
        instance_id: 实例 ID
        timeout: 超时时间（秒），默认 20 分钟
        poll_interval: 轮询间隔（秒），默认 30 秒

    Returns:
        是否就绪
    """
    start_time = time.time()

    while time.time() - start_time < timeout:
        status = get_instance_status(api_instance, instance_id)

        if status:
            print(f"⏳ 实例状态: {status}")

            if status == "Running":
                print("✅ 实例已就绪！")
                return True

        elapsed = int(time.time() - start_time)
        remaining = timeout - elapsed
        print(f"   已等待 {elapsed} 秒，剩余约 {remaining} 秒...")

        time.sleep(poll_interval)

    print("❌ 等待实例就绪超时")
    return False


# 定义可用的规格列表
AVAILABLE_SPECS = [
    {"name": "vedb.mysql.x2.large", "cpu": 2, "memory": 8, "description": "2核8GB"},
    {"name": "vedb.mysql.x4.large", "cpu": 4, "memory": 16, "description": "4核16GB"},
    {
        "name": "vedb.mysql.g4.large",
        "cpu": 4,
        "memory": 16,
        "description": "4核16GB（通用型）",
    },
    {
        "name": "vedb.mysql.p4.large",
        "cpu": 4,
        "memory": 16,
        "description": "4核16GB（性能型）",
    },
    {"name": "vedb.mysql.x4.xlarge", "cpu": 8, "memory": 32, "description": "8核32GB"},
    {"name": "vedb.mysql.x8.large", "cpu": 16, "memory": 64, "description": "16核64GB"},
]

# 默认规格
DEFAULT_SPEC = AVAILABLE_SPECS[0]  # 2核8GB


def parse_spec_input(spec_input: str) -> Optional[Dict]:
    """
    解析用户输入的规格

    Args:
        spec_input: 用户输入的规格字符串，如 "2c8g"、"4c16g" 或完整规格名

    Returns:
        匹配到的规格字典，如果没有匹配则返回 None
    """
    if not spec_input:
        return None

    spec_input = spec_input.strip().lower()

    # 1. 精确匹配完整规格名
    for spec in AVAILABLE_SPECS:
        if spec["name"].lower() == spec_input:
            return spec

    # 2. 解析 "2c8g" 格式
    import re

    match = re.match(r"(\d+)\s*c\s*(\d+)\s*g", spec_input)
    if match:
        cpu = int(match.group(1))
        memory = int(match.group(2))
        return find_closest_spec(cpu, memory)

    # 3. 只提供 CPU 或内存的情况
    match_cpu = re.match(r"(\d+)\s*c", spec_input)
    if match_cpu:
        cpu = int(match_cpu.group(1))
        return find_closest_spec(cpu, None)

    match_mem = re.match(r"(\d+)\s*g", spec_input)
    if match_mem:
        memory = int(match_mem.group(1))
        return find_closest_spec(None, memory)

    return None


def find_closest_spec(
    target_cpu: Optional[int] = None, target_memory: Optional[int] = None
) -> Dict:
    """
    找到最接近的规格

    Args:
        target_cpu: 目标 CPU 核数（可选）
        target_memory: 目标内存大小（GB，可选）

    Returns:
        最接近的规格字典
    """
    if target_cpu is None and target_memory is None:
        return DEFAULT_SPEC

    best_spec = None
    best_score = float("inf")

    for spec in AVAILABLE_SPECS:
        score = 0

        # 计算 CPU 分数（如果提供了目标）
        if target_cpu is not None:
            cpu_diff = spec["cpu"] - target_cpu
            # 如果规格 >= 目标，分数为差值；否则给予较大惩罚
            if cpu_diff >= 0:
                score += cpu_diff * 10
            else:
                score += abs(cpu_diff) * 1000

        # 计算内存分数（如果提供了目标）
        if target_memory is not None:
            mem_diff = spec["memory"] - target_memory
            if mem_diff >= 0:
                score += mem_diff * 1
            else:
                score += abs(mem_diff) * 100

        # 更新最佳匹配
        if score < best_score:
            best_score = score
            best_spec = spec

    return best_spec or DEFAULT_SPEC


def create_vedbm_instance(
    region: str,
    vpc_id: str,
    subnet_id: str,
    zone_id: str,
    instance_name: Optional[str] = None,
    node_spec: Optional[str] = None,
    access_key_id: Optional[str] = None,
    secret_access_key: Optional[str] = None,
) -> Dict:
    """
    创建 VEDBM 实例

    Args:
        region: 区域
        vpc_id: VPC ID
        subnet_id: 子网 ID
        zone_id: 可用区 ID
        instance_name: 实例名称（可选，自动生成）
        node_spec: 节点规格（可选，支持 "2c8g"、"4c16g" 或完整规格名）
        access_key_id: 访问密钥 ID（可选，从环境变量读取）
        secret_access_key: 秘密访问密钥（可选，从环境变量读取）

    Returns:
        创建结果字典
    """
    if instance_name is None:
        instance_name = generate_instance_name()

    # 解析并选择合适的规格
    selected_spec = DEFAULT_SPEC
    if node_spec:
        parsed_spec = parse_spec_input(node_spec)
        if parsed_spec:
            selected_spec = parsed_spec
            print(
                f"✅ 使用规格: {selected_spec['description']} ({selected_spec['name']})"
            )
        else:
            selected_spec = find_closest_spec()
            print(
                f"⚠️  无法解析规格 '{node_spec}'，使用默认规格: {selected_spec['description']}"
            )
    else:
        print(f"✅ 使用默认规格: {selected_spec['description']}")

    # 生成密码
    password = generate_password()

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

    # 创建实例请求
    create_request = CreateDBInstanceRequest(
        charge_type="PostPaid",
        instance_name=instance_name,
        node_spec=selected_spec["name"],
        node_number=2,
        vpc_id=vpc_id,
        subnet_id=subnet_id,
        zone_ids=zone_id,
        db_engine_version="MySQL_8_0",
        super_account_name="root",
        super_account_password=password,
    )

    try:
        # 调用 API 创建实例
        print("⏳ 正在创建 VEDBM 实例...")
        response = api_instance.create_db_instance(create_request)
        instance_id = response.instance_id

        print(f"✅ 实例创建请求已提交，实例 ID: {instance_id}")
        print("⏳ 等待实例就绪（可能需要 5-15 分钟）...")

        # 等待实例就绪
        if wait_for_instance_ready(api_instance, instance_id):
            # 获取连接地址
            print("🔍 正在获取连接地址...")
            endpoint_info = get_instance_endpoint(api_instance, instance_id)

            result = {
                "success": True,
                "instance_id": instance_id,
                "instance_name": instance_name,
                "connection_address": endpoint_info.get("connection_address")
                if endpoint_info
                else None,
                "port": endpoint_info.get("port") if endpoint_info else 3306,
                "username": "root",
                "password": password,
                "region": region,
                "node_spec": selected_spec["name"],
                "node_spec_description": selected_spec["description"],
            }

            return result
        else:
            return {
                "success": False,
                "error": "等待实例就绪超时",
                "instance_id": instance_id,
            }

    except Exception as e:
        return {"success": False, "error": str(e)}


def print_result(result: Dict):
    """打印创建结果"""
    if result["success"]:
        print("\n" + "=" * 60)
        print("✅ VEDBM 实例创建成功！")
        print("=" * 60)
        print(f"实例 ID: {result['instance_id']}")
        print(f"实例名称: {result['instance_name']}")
        if result.get("connection_address"):
            print(f"连接地址: {result['connection_address']}")
            print(f"端口: {result['port']}")
        print(f"用户名: {result['username']}")
        print(f"密码: {result['password']}")
        print(f"区域: {result['region']}")
        print(f"节点规格: {result.get('node_spec_description', result['node_spec'])}")
        print(f"规格代码: {result['node_spec']}")
        print("=" * 60 + "\n")
    else:
        print("\n" + "=" * 60)
        print("❌ VEDBM 实例创建失败！")
        print("=" * 60)
        print(f"错误信息: {result['error']}")
        if result.get("instance_id"):
            print(f"实例 ID: {result['instance_id']}")
        print("=" * 60 + "\n")


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

    # 显示可用规格
    print("📋 可用的节点规格：")
    for spec in AVAILABLE_SPECS:
        print(f"  - {spec['description']}: {spec['name']}")
    print("  提示：也可以使用简写格式，如 '2c8g'、'4c16g'")
    print()

    parser = argparse.ArgumentParser(description="创建火山引擎 VEDBM 实例")
    parser.add_argument(
        "--region",
        default=os.getenv("VEDBM_REGION", "cn-guangzhou"),
        help="区域（默认：cn-guangzhou，或环境变量 VEDBM_REGION）",
    )
    parser.add_argument("--instance-name", help="实例名称（默认自动生成）")
    parser.add_argument(
        "--node-spec",
        default=os.getenv("VEDBM_NODE_SPEC"),
        help="节点规格（可选，默认：2c8g，支持 '2c8g'、'4c16g' 或完整规格名，或环境变量 VEDBM_NODE_SPEC）",
    )
    parser.add_argument(
        "--vpc-id",
        default=os.getenv("VEDBM_VPC_ID"),
        help="VPC ID（必填，或环境变量 VEDBM_VPC_ID）",
    )
    parser.add_argument(
        "--subnet-id",
        default=os.getenv("VEDBM_SUBNET_ID"),
        help="子网 ID（必填，或环境变量 VEDBM_SUBNET_ID）",
    )
    parser.add_argument(
        "--zone-id",
        default=os.getenv("VEDBM_ZONE_ID"),
        help="可用区 ID（必填，或环境变量 VEDBM_ZONE_ID）",
    )

    args = parser.parse_args()

    # 安全提示：AccessKey/SecretKey 仅从环境变量 / .env 读取，不接受命令行参数，
    # 避免凭据暴露在系统进程列表（ps / /proc/<pid>/cmdline）和 Shell 历史记录中。
    access_key_id = os.getenv("VOLCENGINE_ACCESS_KEY")
    secret_access_key = os.getenv("VOLCENGINE_SECRET_KEY")

    # 检查必填参数
    missing = []
    if not args.vpc_id:
        missing.append("VPC ID（--vpc-id 或 VEDBM_VPC_ID）")
    if not args.subnet_id:
        missing.append("子网 ID（--subnet-id 或 VEDBM_SUBNET_ID）")
    if not args.zone_id:
        missing.append("可用区 ID（--zone-id 或 VEDBM_ZONE_ID）")
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
        print("  export VEDBM_VPC_ID=xxx")
        print("  export VEDBM_SUBNET_ID=xxx")
        print("  export VEDBM_ZONE_ID=xxx")
        print("  python create_vedbm_instance.py")
        print("\n方式 2：.env 文件（cp .env.example .env 后填入真实信息）")
        print("\n方式 3：非敏感参数用命令行，AK/SK 仍用环境变量")
        print(
            "  python create_vedbm_instance.py --vpc-id xxx --subnet-id xxx --zone-id xxx"
        )
        sys.exit(1)

    result = create_vedbm_instance(
        region=args.region,
        vpc_id=args.vpc_id,
        subnet_id=args.subnet_id,
        zone_id=args.zone_id,
        instance_name=args.instance_name,
        node_spec=args.node_spec,
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
    )

    print_result(result)

    sys.exit(0 if result["success"] else 1)


if __name__ == "__main__":
    main()
