"""
MQTT 主題常數模組

定義 SDPRS 系統中所有 MQTT 主題字串常數與生成函式。

主題結構：
    sdprs/edge/{node_id}/{category}

方向：
    - Pi → Broker: heartbeat, pump_status, stream_status
    - Server → Pi: cmd/* 指令

使用範例：
    from shared.mqtt_topics import topic_heartbeat, SUB_ALL_HEARTBEAT

    # 發布心跳
    client.publish(topic_heartbeat("glass_node_01"), payload)

    # 訂閱所有心跳
    client.subscribe(SUB_ALL_HEARTBEAT)
"""

# ============================================================
# 主題前綴
# ============================================================
TOPIC_PREFIX = "sdprs/edge"

# ============================================================
# QoS 常數
# ============================================================
QOS_HEARTBEAT = 0       # 心跳遺失可接受
QOS_PUMP_STATUS = 0     # 水泵狀態頻繁發送
QOS_STREAM_STATUS = 1   # 串流狀態需要確認
QOS_CMD = 1             # 指令需要確認送達

# ============================================================
# 訂閱模式常數（中央伺服器用）
# ============================================================
SUB_ALL_HEARTBEAT = "sdprs/edge/+/heartbeat"
SUB_ALL_PUMP_STATUS = "sdprs/edge/+/pump_status"
SUB_ALL_STREAM_STATUS = "sdprs/edge/+/stream_status"


# ============================================================
# 主題生成函式
# ============================================================

def topic_heartbeat(node_id: str) -> str:
    """
    生成心跳主題。

    用於邊緣節點定期發布心跳訊息（每 30 秒）。
    訊息內容包含 cpu_temp, uptime, memory 等。

    Args:
        node_id: 節點識別碼（如 "glass_node_01"）

    Returns:
        完整主題字串，如 "sdprs/edge/glass_node_01/heartbeat"
    """
    return f"{TOPIC_PREFIX}/{node_id}/heartbeat"


def topic_pump_status(node_id: str) -> str:
    """
    生成水泵狀態主題。

    用於 ESP32/Pico W 定期發布水泵狀態（每 10 秒）。
    訊息內容包含 pump_state, water_level。

    Args:
        node_id: 節點識別碼（如 "pump_node_01"）

    Returns:
        完整主題字串，如 "sdprs/edge/pump_node_01/pump_status"
    """
    return f"{TOPIC_PREFIX}/{node_id}/pump_status"


def topic_stream_status(node_id: str) -> str:
    """
    生成串流狀態主題。

    用於邊緣節點發布 HLS 串流狀態（事件驅動）。
    訊息內容包含 active/stopped, tunnel_port。

    Args:
        node_id: 節點識別碼（如 "glass_node_01"）

    Returns:
        完整主題字串，如 "sdprs/edge/glass_node_01/stream_status"
    """
    return f"{TOPIC_PREFIX}/{node_id}/stream_status"


def topic_cmd_stream_start(node_id: str) -> str:
    """
    生成啟動串流指令主題。

    用於中央伺服器發送啟動 HLS 串流指令給邊緣節點。

    Args:
        node_id: 節點識別碼（如 "glass_node_01"）

    Returns:
        完整主題字串，如 "sdprs/edge/glass_node_01/cmd/stream_start"
    """
    return f"{TOPIC_PREFIX}/{node_id}/cmd/stream_start"


def topic_cmd_stream_stop(node_id: str) -> str:
    """
    生成停止串流指令主題。

    用於中央伺服器發送停止 HLS 串流指令給邊緣節點。

    Args:
        node_id: 節點識別碼（如 "glass_node_01"）

    Returns:
        完整主題字串，如 "sdprs/edge/glass_node_01/cmd/stream_stop"
    """
    return f"{TOPIC_PREFIX}/{node_id}/cmd/stream_stop"


def topic_cmd_update(node_id: str) -> str:
    """
    生成更新指令主題。

    用於中央伺服器觸發邊緣節點自動更新（git pull + restart）。

    Args:
        node_id: 節點識別碼（如 "glass_node_01"）

    Returns:
        完整主題字串，如 "sdprs/edge/glass_node_01/cmd/update"
    """
    return f"{TOPIC_PREFIX}/{node_id}/cmd/update"


def topic_cmd_simulate_trigger(node_id: str) -> str:
    """
    生成模擬觸發指令主題。

    用於中央伺服器觸發邊緣節點模擬觸發測試。

    Args:
        node_id: 節點識別碼（如 "glass_node_01"）

    Returns:
        完整主題字串，如 "sdprs/edge/glass_node_01/cmd/simulate_trigger"
    """
    return f"{TOPIC_PREFIX}/{node_id}/cmd/simulate_trigger"


def sub_cmd_all(node_id: str) -> str:
    """
    生成該節點所有指令的訂閱模式。

    用於邊緣節點訂閱所有發送給自己的指令。

    Args:
        node_id: 節點識別碼（如 "glass_node_01"）

    Returns:
        訂閱模式字串，如 "sdprs/edge/glass_node_01/cmd/#"
    """
    return f"{TOPIC_PREFIX}/{node_id}/cmd/#"


# ============================================================
# 測試區塊
# ============================================================
if __name__ == "__main__":
    print("=" * 60)
    print("MQTT 主題常數模組測試")
    print("=" * 60)

    # 測試用節點 ID
    glass_node = "glass_node_01"
    pump_node = "pump_node_01"

    print(f"\n--- 玻璃節點: {glass_node} ---")
    print(f"心跳主題:       {topic_heartbeat(glass_node)}")
    print(f"串流狀態主題:   {topic_stream_status(glass_node)}")
    print(f"啟動串流指令:   {topic_cmd_stream_start(glass_node)}")
    print(f"停止串流指令:   {topic_cmd_stream_stop(glass_node)}")
    print(f"更新指令:       {topic_cmd_update(glass_node)}")
    print(f"模擬觸發指令:   {topic_cmd_simulate_trigger(glass_node)}")
    print(f"指令訂閱模式:   {sub_cmd_all(glass_node)}")

    print(f"\n--- 水泵節點: {pump_node} ---")
    print(f"水泵狀態主題:   {topic_pump_status(pump_node)}")
    print(f"指令訂閱模式:   {sub_cmd_all(pump_node)}")

    print("\n--- 中央伺服器訂閱模式 ---")
    print(f"所有心跳:       {SUB_ALL_HEARTBEAT}")
    print(f"所有水泵狀態:   {SUB_ALL_PUMP_STATUS}")
    print(f"所有串流狀態:   {SUB_ALL_STREAM_STATUS}")

    print("\n--- QoS 常數 ---")
    print(f"心跳 QoS:       {QOS_HEARTBEAT}")
    print(f"水泵狀態 QoS:   {QOS_PUMP_STATUS}")
    print(f"串流狀態 QoS:   {QOS_STREAM_STATUS}")
    print(f"指令 QoS:       {QOS_CMD}")

    print("\n" + "=" * 60)