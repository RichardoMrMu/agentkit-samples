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
火山引擎 VEDBM 实例规格升级脚本
支持用户指定规格或自动升级一个等级
"""

import argparse
import os
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
        DescribeDBInstancesRequest,
        ModifyDBInstanceSpecRequest,
    )
except ImportError as e:
    print(f"错误：未安装火山引擎 Python SDK 或导入失败: {e}")
    import traceback

    traceback.print_exc()
    print("请运行: pip install volcengine-python-sdk")
    sys.exit(1)


# 定义可用的规格列表，按类型分组
# 基于实际API返回的规格
SPEC_GROUPS = {
    "g4": [
        {
            "name": "vedb.mysql.g2.medium",
            "cpu": 2,
            "memory": 8,
            "description": "2核8GB（通用型）",
        },
        {
            "name": "vedb.mysql.g4.large",
            "cpu": 4,
            "memory": 16,
            "description": "4核16GB（通用型）",
        },
        {
            "name": "vedb.mysql.g4.xlarge",
            "cpu": 8,
            "memory": 32,
            "description": "8核32GB（通用型）",
        },
        {
            "name": "vedb.mysql.g4.2xlarge",
            "cpu": 16,
            "memory": 64,
            "description": "16核64GB（通用型）",
        },
        {
            "name": "vedb.mysql.g4.4xlarge",
            "cpu": 32,
            "memory": 128,
            "description": "32核128GB（通用型）",
        },
    ],
    "x4": [
        {
            "name": "vedb.mysql.x4.large",
            "cpu": 4,
            "memory": 16,
            "description": "4核16GB（标准型）",
        },
        {
            "name": "vedb.mysql.x4.xlarge",
            "cpu": 8,
            "memory": 32,
            "description": "8核32GB（标准型）",
        },
        {
            "name": "vedb.mysql.x4.2xlarge",
            "cpu": 16,
            "memory": 64,
            "description": "16核64GB（标准型）",
        },
        {
            "name": "vedb.mysql.x4.4xlarge",
            "cpu": 32,
            "memory": 128,
            "description": "32核128GB（标准型）",
        },
        {
            "name": "vedb.mysql.x4.8xlarge",
            "cpu": 64,
            "memory": 256,
            "description": "64核256GB（标准型）",
        },
    ],
    "x8": [
        {
            "name": "vedb.mysql.x8.large",
            "cpu": 8,
            "memory": 32,
            "description": "8核32GB（标准型X8）",
        },
        {
            "name": "vedb.mysql.x8.xlarge",
            "cpu": 16,
            "memory": 64,
            "description": "16核64GB（标准型X8）",
        },
        {
            "name": "vedb.mysql.x8.2xlarge",
            "cpu": 32,
            "memory": 128,
            "description": "32核128GB（标准型X8）",
        },
        {
            "name": "vedb.mysql.x8.4xlarge",
            "cpu": 64,
            "memory": 256,
            "description": "64核256GB（标准型X8）",
        },
        {
            "name": "vedb.mysql.x8.6xlarge",
            "cpu": 96,
            "memory": 384,
            "description": "96核384GB（标准型X8）",
        },
        {
            "name": "vedb.mysql.x8.8xlarge",
            "cpu": 128,
            "memory": 512,
            "description": "128核512GB（标准型X8）",
        },
    ],
    "g8": [
        {
            "name": "vedb.mysql.g8.2xlarge",
            "cpu": 16,
            "memory": 64,
            "description": "16核64GB（通用型G8）",
        },
    ],
}


def get_spec_type(spec_name: str) -> str:
    """
    获取规格类型

    Args:
        spec_name: 规格名称

    Returns:
        规格类型（g4/x4/x8/g8）
    """
    if not spec_name:
        return "g4"

    if "g8" in spec_name:
        return "g8"
    elif "g4" in spec_name:
        return "g4"
    elif "x8" in spec_name:
        return "x8"
    elif "x4" in spec_name:
        return "x4"
    elif "g2" in spec_name:
        return "g4"  # g2 映射到 g4
    elif "x2" in spec_name:
        return "x4"  # x2 映射到 x4
    elif "g" in spec_name:
        return "g4"
    elif "x" in spec_name:
        return "x4"
    else:
        return "g4"


def get_spec_info(spec_name: str) -> Optional[Dict]:
    """
    获取规格信息

    Args:
        spec_name: 规格名称

    Returns:
        规格信息字典
    """
    for group in SPEC_GROUPS.values():
        for spec in group:
            if spec["name"] == spec_name:
                return spec
    return None


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
    for group in SPEC_GROUPS.values():
        for spec in group:
            if spec["name"].lower() == spec_input:
                return spec

    # 2. 解析 "2c8g" 格式
    import re

    match = re.match(r"(\d+)\s*c\s*(\d+)\s*g", spec_input)
    if match:
        cpu = int(match.group(1))
        memory = int(match.group(2))
        # 默认在 g4 系列中查找
        return find_closest_spec(cpu, memory, "g4")

    # 3. 只提供 CPU 或内存的情况
    match_cpu = re.match(r"(\d+)\s*c", spec_input)
    if match_cpu:
        cpu = int(match_cpu.group(1))
        return find_closest_spec(cpu, None, "g4")

    match_mem = re.match(r"(\d+)\s*g", spec_input)
    if match_mem:
        memory = int(match_mem.group(1))
        return find_closest_spec(None, memory, "g4")

    return None


def find_closest_spec(
    target_cpu: Optional[int] = None,
    target_memory: Optional[int] = None,
    spec_type: str = "g4",
) -> Optional[Dict]:
    """
    找到最接近的规格

    Args:
        target_cpu: 目标 CPU 核数（可选）
        target_memory: 目标内存大小（GB，可选）
        spec_type: 规格类型（g4/x4/x8/g8）

    Returns:
        最接近的规格字典
    """
    specs = SPEC_GROUPS.get(spec_type, SPEC_GROUPS["g4"])

    if target_cpu is None and target_memory is None:
        return specs[0] if specs else None

    best_spec = None
    best_score = float("inf")

    for spec in specs:
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

    return best_spec


def get_next_higher_spec(current_spec_name: str) -> Optional[Dict]:
    """
    获取当前规格的下一个更高规格（同一类型内升级）

    Args:
        current_spec_name: 当前规格名称

    Returns:
        下一个更高规格，如果已经是最大规格则返回 None
    """
    current_spec = get_spec_info(current_spec_name)
    if not current_spec:
        return None

    spec_type = get_spec_type(current_spec_name)
    specs = SPEC_GROUPS.get(spec_type, [])

    # 找到当前规格在列表中的位置
    current_index = -1
    for i, spec in enumerate(specs):
        if spec["name"] == current_spec_name:
            current_index = i
            break

    if current_index == -1:
        return None

    # 如果已经是最大规格
    if current_index >= len(specs) - 1:
        return None

    # 返回下一个规格
    return specs[current_index + 1]


def get_instance_info(api_instance: VEDBMApi, instance_id: str) -> Optional[Dict]:
    """
    获取实例详细信息

    Args:
        api_instance: VEDBM API 实例
        instance_id: 实例 ID

    Returns:
        实例信息字典
    """
    try:
        request = DescribeDBInstancesRequest(instance_id=instance_id)
        response = api_instance.describe_db_instances(request)

        if not response.instances or len(response.instances) == 0:
            return None

        instance = response.instances[0]

        # 从 nodes 数组中获取节点规格
        node_spec = None
        node_number = 0
        if hasattr(instance, "nodes") and instance.nodes:
            node_number = len(instance.nodes)
            for node in instance.nodes:
                if hasattr(node, "node_spec") and node.node_spec:
                    node_spec = node.node_spec
                    break

        return {
            "instance_id": getattr(instance, "instance_id", None),
            "instance_name": getattr(instance, "instance_name", None),
            "instance_status": getattr(instance, "instance_status", None),
            "node_spec": node_spec,
            "node_number": node_number,
            "region": getattr(instance, "region_id", None),
            "zone_id": getattr(instance, "zone_ids", None),
            "create_time": getattr(instance, "create_time", None),
            "spec_family": getattr(instance, "spec_family", None),
        }

    except Exception as e:
        print(f"⚠️  查询实例信息失败: {e}")
        return None


def wait_for_spec_change_complete(
    api_instance: VEDBMApi,
    instance_id: str,
    target_spec: str,
    timeout: int = 600,
    poll_interval: int = 30,
) -> Dict:
    """
    等待规格变更完成

    Args:
        api_instance: VEDBM API 实例
        instance_id: 实例 ID
        target_spec: 目标规格
        timeout: 超时时间（秒），默认 10 分钟
        poll_interval: 轮询间隔（秒），默认 30 秒

    Returns:
        包含完成状态和最终规格信息的字典
    """
    start_time = time.time()

    while time.time() - start_time < timeout:
        instance_info = get_instance_info(api_instance, instance_id)

        if instance_info:
            current_status = instance_info.get("instance_status", "Unknown")
            current_spec = instance_info.get("node_spec", "Unknown")

            print(f"⏳ 实例状态: {current_status}, 当前规格: {current_spec}")

            # 如果状态是 Running，检查规格是否已更新
            if current_status == "Running":
                if current_spec == target_spec:
                    print("✅ 规格变更完成！")
                    return {
                        "completed": True,
                        "final_spec": current_spec,
                        "final_status": current_status,
                        "timeout": False,
                    }
                else:
                    # 状态是 Running 但规格还没变，继续等待
                    pass

        elapsed = int(time.time() - start_time)
        remaining = timeout - elapsed
        print(f"   已等待 {elapsed} 秒，剩余约 {remaining} 秒...")

        time.sleep(poll_interval)

    # 超时了，获取最后的实例信息
    final_instance_info = get_instance_info(api_instance, instance_id)
    final_spec = final_instance_info.get("node_spec") if final_instance_info else None
    final_status = (
        final_instance_info.get("instance_status") if final_instance_info else None
    )

    print("⚠️  等待规格变更超时（10分钟）")
    return {
        "completed": False,
        "final_spec": final_spec,
        "final_status": final_status,
        "timeout": True,
    }


def upgrade_instance_spec(
    region: str,
    instance_id: str,
    target_spec: Optional[str] = None,
    access_key_id: Optional[str] = None,
    secret_access_key: Optional[str] = None,
) -> Dict:
    """
    升级实例规格

    Args:
        region: 区域
        instance_id: 实例 ID
        target_spec: 目标规格（可选，如果不提供则自动升级一个等级）
        access_key_id: 访问密钥 ID（可选，从环境变量读取）
        secret_access_key: 秘密访问密钥（可选，从环境变量读取）

    Returns:
        升级结果字典
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

    # 第一步：获取升级前的实例信息
    print(f"🔍 正在查询实例 {instance_id} 的信息...")
    before_instance_info = get_instance_info(api_instance, instance_id)

    if not before_instance_info:
        return {"success": False, "error": f"无法获取实例 {instance_id} 的信息"}

    before_spec = before_instance_info.get("node_spec")
    before_status = before_instance_info.get("instance_status")
    before_spec_info = get_spec_info(before_spec)

    print(f"✅ 升级前 - 实例状态: {before_status}")
    print(f"✅ 升级前 - 规格: {before_spec}")
    if before_spec_info:
        print(
            f"✅ 升级前 - 配置: {before_spec_info['cpu']}核 {before_spec_info['memory']}GB"
        )

    # 检查实例状态
    if before_status != "Running":
        return {
            "success": False,
            "error": f"实例状态不是 Running（当前状态: {before_status}），无法进行规格变更",
        }

    # 第二步：确定目标规格
    selected_spec = None

    if target_spec:
        # 用户指定了规格
        parsed_spec = parse_spec_input(target_spec)
        if parsed_spec:
            selected_spec = parsed_spec
            print(
                f"✅ 使用用户指定的规格: {selected_spec['description']} ({selected_spec['name']})"
            )
        else:
            return {"success": False, "error": f"无法解析规格 '{target_spec}'"}
    else:
        # 自动升级一个等级
        current_spec_info = get_spec_info(before_spec)

        if not current_spec_info:
            # 当前规格不在我们的列表中，可能是旧规格
            spec_type = get_spec_type(before_spec)
            specs = SPEC_GROUPS.get(spec_type, [])

            if not specs:
                return {
                    "success": False,
                    "error": f"无法识别当前规格 '{before_spec}'，且找不到对应的规格系列。请手动指定目标规格。",
                }

            # 对于旧规格，我们选择该系列的最小规格作为升级起点
            print(f"⚠️  当前规格 '{before_spec}' 是旧规格，不在可用列表中")
            print("   建议升级到同系列的可用规格")

            # 选择该系列的最小规格
            selected_spec = specs[0]
            print(
                f"✅ 建议升级到: {selected_spec['description']} ({selected_spec['name']})"
            )
        else:
            # 当前规格在我们的列表中，正常升级
            next_spec = get_next_higher_spec(before_spec)
            if not next_spec:
                spec_type = get_spec_type(before_spec)
                type_name = {
                    "g4": "G4（通用型）",
                    "x4": "X4（标准型）",
                    "x8": "X8（标准型X8）",
                    "g8": "G8（通用型G8）",
                }.get(spec_type, spec_type.upper())
                return {
                    "success": False,
                    "error": f"当前规格 '{before_spec}' 已经是 {type_name} 系列的最大规格，无法继续升级",
                }

            selected_spec = next_spec
            print(
                f"✅ 自动选择下一个规格: {selected_spec['description']} ({selected_spec['name']})"
            )

    # 检查是否跨规格类型
    before_spec_type = get_spec_type(before_spec)
    target_spec_type = get_spec_type(selected_spec["name"])

    if before_spec_type != target_spec_type:
        before_type_name = {
            "g4": "G4（通用型）",
            "x4": "X4（标准型）",
            "x8": "X8（标准型X8）",
            "g8": "G8（通用型G8）",
        }.get(before_spec_type, before_spec_type.upper())
        target_type_name = {
            "g4": "G4（通用型）",
            "x4": "X4（标准型）",
            "x8": "X8（标准型X8）",
            "g8": "G8（通用型G8）",
        }.get(target_spec_type, target_spec_type.upper())
        return {
            "success": False,
            "error": f"不支持跨规格类型升级（当前: {before_type_name}, 目标: {target_type_name}）",
        }

    # 检查是否需要变更
    if before_spec == selected_spec["name"]:
        return {
            "success": True,
            "message": f"当前规格已经是 {selected_spec['description']}，无需变更",
            "instance_id": instance_id,
            "before_spec": before_spec,
            "before_spec_info": before_spec_info,
            "after_spec": selected_spec["name"],
            "after_spec_info": selected_spec,
        }

    # 第三步：执行规格变更
    try:
        print("⏳ 正在提交规格变更请求...")
        print(f"   从: {before_spec}")
        print(f"   到: {selected_spec['name']} ({selected_spec['description']})")
        print(f"   节点数量: {before_instance_info.get('node_number', 2)}")

        modify_request = ModifyDBInstanceSpecRequest(
            instance_id=instance_id,
            node_spec=selected_spec["name"],
            node_number=before_instance_info.get("node_number", 2),
        )

        api_instance.modify_db_instance_spec(modify_request)

        print("✅ 规格变更请求已提交")

        # 第四步：等待变更完成（最多10分钟）
        print("⏳ 等待规格变更完成（最多10分钟）...")
        print("   提示：通过检查实例状态是否为 Running 来判断是否完成")

        wait_result = wait_for_spec_change_complete(
            api_instance, instance_id, selected_spec["name"]
        )

        # 获取升级后的规格信息
        after_spec_info = get_spec_info(wait_result.get("final_spec"))

        result = {
            "success": True,
            "instance_id": instance_id,
            "before_spec": before_spec,
            "before_spec_info": before_spec_info,
            "after_spec": wait_result.get("final_spec"),
            "after_spec_info": after_spec_info,
            "target_spec": selected_spec["name"],
            "target_spec_info": selected_spec,
            "completed": wait_result.get("completed", False),
            "timeout": wait_result.get("timeout", False),
            "final_status": wait_result.get("final_status"),
        }

        if wait_result.get("timeout"):
            result["message"] = (
                "规格变更请求已提交，但等待超时（10分钟）。请通过火山引擎控制台关注任务状态。"
            )

        return result

    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "instance_id": instance_id,
            "before_spec": before_spec,
            "before_spec_info": before_spec_info,
        }


def print_result(result: Dict):
    """打印升级结果"""
    if result["success"]:
        print("\n" + "=" * 100)

        if result.get("message") and "无需变更" in result["message"]:
            print("ℹ️  规格变更 - 无需操作")
            print("=" * 100)
            print(result["message"])
        elif result.get("completed", False):
            print("✅ VEDBM 实例规格升级成功！")
            print("=" * 100)
            print(f"实例 ID: {result['instance_id']}")
            print()
            print("📋 升级前：")
            print("-" * 100)
            print(f"  规格: {result['before_spec']}")
            if result.get("before_spec_info"):
                print(
                    f"  配置: {result['before_spec_info']['cpu']}核 {result['before_spec_info']['memory']}GB"
                )
                print(f"  描述: {result['before_spec_info']['description']}")
            print()
            print("🚀 升级后：")
            print("-" * 100)
            print(f"  规格: {result['after_spec']}")
            if result.get("after_spec_info"):
                print(
                    f"  配置: {result['after_spec_info']['cpu']}核 {result['after_spec_info']['memory']}GB"
                )
                print(f"  描述: {result['after_spec_info']['description']}")
        else:
            print("⏳ VEDBM 实例规格变更请求已提交")
            print("=" * 100)
            print(f"实例 ID: {result['instance_id']}")
            print()
            print("📋 升级前：")
            print("-" * 100)
            print(f"  规格: {result['before_spec']}")
            if result.get("before_spec_info"):
                print(
                    f"  配置: {result['before_spec_info']['cpu']}核 {result['before_spec_info']['memory']}GB"
                )
                print(f"  描述: {result['before_spec_info']['description']}")
            print()
            print("🎯 目标规格：")
            print("-" * 100)
            print(f"  规格: {result['target_spec']}")
            if result.get("target_spec_info"):
                print(
                    f"  配置: {result['target_spec_info']['cpu']}核 {result['target_spec_info']['memory']}GB"
                )
                print(f"  描述: {result['target_spec_info']['description']}")
            print()
            if result.get("after_spec"):
                print("📊 当前状态：")
                print("-" * 100)
                print(f"  实例状态: {result.get('final_status', 'Unknown')}")
                print(f"  当前规格: {result['after_spec']}")
            print()
            print(
                f"⚠️  注意: {result.get('message', '等待超时，请通过控制台关注任务状态')}"
            )

        print("=" * 100 + "\n")
    else:
        print("\n" + "=" * 100)
        print("❌ VEDBM 实例规格升级失败！")
        print("=" * 100)
        print(f"错误信息: {result['error']}")
        if result.get("instance_id"):
            print(f"实例 ID: {result['instance_id']}")
        if result.get("before_spec"):
            print()
            print("📋 升级前规格：")
            print("-" * 100)
            print(f"  规格: {result['before_spec']}")
            if result.get("before_spec_info"):
                print(
                    f"  配置: {result['before_spec_info']['cpu']}核 {result['before_spec_info']['memory']}GB"
                )
                print(f"  描述: {result['before_spec_info']['description']}")
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

    # 显示可用规格
    print("📋 可用的节点规格（按类型分组）：")
    print()
    for spec_type, specs in SPEC_GROUPS.items():
        type_name = {
            "g4": "G4 系列（通用型，默认）",
            "x4": "X4 系列（标准型）",
            "x8": "X8 系列（标准型X8）",
            "g8": "G8 系列（通用型G8）",
        }.get(spec_type, f"{spec_type.upper()} 系列")
        print(f"  {type_name}:")
        for spec in specs:
            print(f"    - {spec['description']}: {spec['name']}")
        print()
    print("  提示：也可以使用简写格式，如 '4c16g'、'8c32g'")
    print("  注意：不支持跨系列升级！")
    print()

    parser = argparse.ArgumentParser(description="升级火山引擎 VEDBM 实例规格")
    parser.add_argument(
        "--region",
        default=os.getenv("VEDBM_REGION", "cn-guangzhou"),
        help="区域（默认：cn-guangzhou，或环境变量 VEDBM_REGION）",
    )
    parser.add_argument("--instance-id", required=True, help="实例 ID（必填）")
    parser.add_argument(
        "--target-spec",
        help="目标规格（可选，不提供则自动升级一个等级，支持 '4c16g'、'8c32g' 或完整规格名）",
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
        print("\n⚠️  为避免凭据泄露，AK/SK 仅支持通过环境变量或 .env 文件提供，")
        print("    不要作为命令行参数传入（会暴露在进程列表和 Shell 历史中）。")
        print("\n方式 1：环境变量")
        print("  export VOLCENGINE_ACCESS_KEY=xxx")
        print("  export VOLCENGINE_SECRET_KEY=xxx")
        print("  python upgrade_instance_spec.py --instance-id xxx")
        print("\n方式 2：.env 文件 + 命令行非敏感参数")
        print(
            "  python upgrade_instance_spec.py --instance-id xxx --target-spec 16c64g"
        )
        sys.exit(1)

    result = upgrade_instance_spec(
        region=args.region,
        instance_id=args.instance_id,
        target_spec=args.target_spec,
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
    )

    print_result(result)

    sys.exit(0 if result["success"] else 1)


if __name__ == "__main__":
    main()
