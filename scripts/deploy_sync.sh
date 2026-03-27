#!/usr/bin/env bash
# ============================================================
# SDPRS Deploy Sync Script
# 使用 rsync 將本地代碼同步到 Raspberry Pi 並重啟服務
# ============================================================
set -euo pipefail

# ===== 配置 =====
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
SSH_USER="${SDPRS_SSH_USER:-pi}"
SERVER_HOST="${SDPRS_SERVER_HOST:-sdprs-server.local}"
SERVER_DEPLOY_PATH="/opt/sdprs"

# venv 路徑（在 Pi 上由 init 建立，不從開發機同步）
SERVER_VENV="${SERVER_DEPLOY_PATH}/central_server/venv"
EDGE_VENV="${SERVER_DEPLOY_PATH}/edge_glass/venv"

# 通用排除列表
COMMON_EXCLUDES=(
    --exclude '.env'
    --exclude '*.pyc'
    --exclude '__pycache__/'
    --exclude '.git/'
    --exclude '.gitignore'
    --exclude 'venv/'
    --exclude '.venv/'
    --exclude 'node_modules/'
    --exclude '*.egg-info/'
)
# 注意：.env.example 不排除，它是範本檔案需要同步

# 伺服器額外排除
SERVER_EXCLUDES=(
    --exclude 'data/'
    --exclude 'storage/'
)

# 邊緣節點額外排除
EDGE_EXCLUDES=(
    --exclude 'events/'
    --exclude 'data/'
)

# 顏色輸出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

log_info()  { echo -e "${CYAN}[INFO]${NC} $*"; }
log_ok()    { echo -e "${GREEN}[OK]${NC} $*"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $*"; }

# ===== 函數 =====

show_usage() {
    cat <<'USAGE'
用法:
  deploy_sync.sh init-server             首次初始化中央伺服器環境
  deploy_sync.sh init-glass <NN>         首次初始化邊緣節點環境
  deploy_sync.sh server                  同步代碼到中央伺服器並重啟
  deploy_sync.sh glass <NN>              同步代碼到邊緣節點 NN 並重啟
  deploy_sync.sh all                     同步到所有已配置的節點
  deploy_sync.sh --dry-run server        預覽同步（不實際執行）
  deploy_sync.sh --help                  顯示此說明

首次部署流程:
  1. deploy_sync.sh init-server          # 初始化（建 venv、裝依賴、設 systemd）
  2. ssh pi@<host> 'nano /opt/sdprs/.env'  # 修改密碼
  3. deploy_sync.sh server               # 之後每次代碼更新只需這步

環境變數:
  SDPRS_SSH_USER          SSH 用戶名 (預設: pi)
  SDPRS_SERVER_HOST       中央伺服器地址 (預設: sdprs-server.local)
  SDPRS_GLASS_HOST        邊緣節點地址 (預設: sdprs-glass-NN.local)
  SDPRS_GLASS_NODES       邊緣節點列表，逗號分隔 (預設: 01)
                          例: SDPRS_GLASS_NODES="01,02,03"

範例:
  ./deploy_sync.sh init-server                               # 首次初始化伺服器
  ./deploy_sync.sh server                                    # 日常更新伺服器
  SDPRS_SERVER_HOST=192.168.1.100 ./deploy_sync.sh server    # 用 IP 連線伺服器
  SDPRS_GLASS_HOST=192.168.1.101 ./deploy_sync.sh init-glass 01  # 首次初始化邊緣節點
  SDPRS_GLASS_HOST=192.168.1.101 ./deploy_sync.sh glass 01       # 日常更新邊緣節點
USAGE
}

check_ssh_connection() {
    local host="$1"
    local user="$2"
    log_info "檢查 SSH 連線: ${user}@${host} ..."
    if ! ssh -o ConnectTimeout=10 "${user}@${host}" "echo ok" </dev/null; then
        log_error "無法連線到 ${user}@${host}"
        log_warn "請確認:"
        log_warn "  1. 設備已開機且連接網路"
        log_warn "  2. SSH 服務已啟動"
        log_warn "  3. SSH 金鑰已配置（或可用密碼登入）"
        log_warn ""
        log_warn "建議配置免密碼 SSH 金鑰登入:"
        log_warn "  ssh-copy-id ${user}@${host}"
        return 1
    fi
    log_ok "SSH 連線正常"
}

sync_to_host() {
    local host="$1"
    local user="$2"
    local deploy_path="$3"
    shift 3
    local extra_excludes=("$@")

    local rsync_args=(
        -avz
        --delete
        "${COMMON_EXCLUDES[@]}"
        "${extra_excludes[@]}"
    )

    if [ "${DRY_RUN:-false}" = "true" ]; then
        rsync_args+=(--dry-run)
        log_warn "預覽模式 (dry-run)，不會實際修改檔案"
    fi

    log_info "同步 ${PROJECT_DIR}/ -> ${user}@${host}:${deploy_path}/"
    echo ""

    rsync "${rsync_args[@]}" \
        "${PROJECT_DIR}/" \
        "${user}@${host}:${deploy_path}/"

    echo ""
    log_ok "同步完成"
}

restart_service() {
    local host="$1"
    local user="$2"
    local service="$3"

    if [ "${DRY_RUN:-false}" = "true" ]; then
        log_info "[dry-run] 將重啟: ${service}"
        return 0
    fi

    # 檢查服務是否已安裝
    if ! ssh "${user}@${host}" "systemctl list-unit-files ${service}.service" </dev/null | grep -q "${service}"; then
        log_warn "服務 ${service} 尚未安裝，跳過重啟"
        log_warn "請先執行: deploy_sync.sh init-server"
        return 0
    fi

    log_info "重啟服務: ${service} ..."
    ssh "${user}@${host}" "sudo systemctl daemon-reload && sudo systemctl restart ${service}" </dev/null

    # 等待 3 秒後檢查狀態
    sleep 3
    local status
    status=$(ssh "${user}@${host}" "systemctl is-active ${service}" </dev/null 2>/dev/null || true)

    if [ "$status" = "active" ]; then
        log_ok "${service} 運行正常 (active)"
    else
        log_error "${service} 狀態異常: ${status}"
        log_warn "查看日誌: ssh ${user}@${host} 'journalctl -u ${service} --since \"2 minutes ago\" --no-pager'"
        return 1
    fi
}

# ===== 初始化命令 =====

init_server() {
    local host="${SERVER_HOST}"
    local user="${SSH_USER}"

    echo ""
    log_info "========== 首次初始化中央伺服器 (${host}) =========="
    echo ""

    check_ssh_connection "$host" "$user" || return 1

    # 確保目標目錄存在且有權限
    log_info "準備目標目錄 ..."
    ssh "${user}@${host}" "sudo mkdir -p ${SERVER_DEPLOY_PATH} && sudo chown ${user}:${user} ${SERVER_DEPLOY_PATH}" </dev/null

    # 同步代碼
    sync_to_host "$host" "$user" "$SERVER_DEPLOY_PATH" "${SERVER_EXCLUDES[@]}"

    # 檢查是否有 SSH 金鑰認證（密碼認證 + heredoc 會卡住）
    if ! ssh -o BatchMode=yes -o ConnectTimeout=5 "${user}@${host}" "true" </dev/null 2>/dev/null; then
        log_warn "偵測到密碼認證（非金鑰認證）"
        log_warn "init 命令需要 SSH 金鑰認證才能自動執行"
        log_warn ""
        log_warn "請先配置 SSH 金鑰："
        log_warn "  ssh-copy-id ${user}@${host}"
        log_warn ""
        log_warn "或手動在 Pi 上執行初始化步驟（見 README 七、步驟 3-6）"
        return 1
    fi

    log_info "在 Pi 上建立環境 ..."
    ssh "${user}@${host}" bash </dev/null <<REMOTE_INIT
set -e

# 建立系統用戶
sudo useradd -r -s /bin/false -d ${SERVER_DEPLOY_PATH} sdprs 2>/dev/null || true

# 安裝系統依賴
export DEBIAN_FRONTEND=noninteractive
sudo -E apt-get update -qq
sudo -E apt-get install -y -qq python3-full python3-venv python3-dev mosquitto sqlite3 > /dev/null

# 修復 piwheels 連線問題（Pi OS 預設的 extra-index-url 常連線失敗）
if [ -f /etc/pip.conf ]; then
    sudo mv /etc/pip.conf /etc/pip.conf.bak
    echo "[INFO] 已備份 /etc/pip.conf -> /etc/pip.conf.bak (移除 piwheels)"
fi

# 建立 venv 並安裝依賴（必須在 Pi 上建立，不能從開發機同步）
python3 -m venv ${SERVER_VENV}
${SERVER_VENV}/bin/pip install --upgrade pip -q
${SERVER_VENV}/bin/pip install -r ${SERVER_DEPLOY_PATH}/central_server/requirements.txt --prefer-binary -q

# 建立資料和儲存目錄
sudo mkdir -p ${SERVER_DEPLOY_PATH}/data ${SERVER_DEPLOY_PATH}/storage

# 建立 .env（如果不存在）
if [ ! -f ${SERVER_DEPLOY_PATH}/.env ]; then
    cp ${SERVER_DEPLOY_PATH}/.env.example ${SERVER_DEPLOY_PATH}/.env
    echo "[WARN] 已建立 .env，請務必修改密碼！"
fi

# 安裝 systemd 服務
sudo cp ${SERVER_DEPLOY_PATH}/central_server/systemd/sdprs-server.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable sdprs-server mosquitto

# 設定權限
sudo chown -R sdprs:sdprs ${SERVER_DEPLOY_PATH}

# 啟動服務
sudo systemctl restart mosquitto
sudo systemctl restart sdprs-server

echo "[OK] 初始化完成"
REMOTE_INIT

    sleep 3
    local status
    status=$(ssh "${user}@${host}" "systemctl is-active sdprs-server" </dev/null 2>/dev/null || true)
    if [ "$status" = "active" ]; then
        log_ok "sdprs-server 運行正常!"
    else
        log_warn "sdprs-server 狀態: ${status}"
        log_warn "查看日誌: ssh ${user}@${host} 'journalctl -u sdprs-server --since \"2 minutes ago\" --no-pager'"
    fi

    echo ""
    log_info "下一步:"
    log_info "  1. ssh ${user}@${host} 'sudo nano ${SERVER_DEPLOY_PATH}/.env'  # 修改密碼"
    log_info "  2. ssh ${user}@${host} 'sudo systemctl restart sdprs-server'   # 重啟生效"
    log_info "  3. 瀏覽器打開 http://${host}:8000                              # 測試儀表板"
    echo ""
}

init_glass() {
    local node_num="$1"
    local node_id="glass_node_${node_num}"
    local host="${SDPRS_GLASS_HOST:-sdprs-glass-${node_num}.local}"
    local user="${SSH_USER}"

    echo ""
    log_info "========== 首次初始化邊緣節點 ${node_id} (${host}) =========="
    echo ""

    check_ssh_connection "$host" "$user" || return 1

    # 確保目標目錄存在
    ssh "${user}@${host}" "sudo mkdir -p ${SERVER_DEPLOY_PATH} && sudo chown ${user}:${user} ${SERVER_DEPLOY_PATH}" </dev/null

    # 同步代碼
    sync_to_host "$host" "$user" "$SERVER_DEPLOY_PATH" "${EDGE_EXCLUDES[@]}"

    # 檢查是否有 SSH 金鑰認證（密碼認證 + heredoc 會卡住）
    if ! ssh -o BatchMode=yes -o ConnectTimeout=5 "${user}@${host}" "true" </dev/null 2>/dev/null; then
        log_warn "偵測到密碼認證（非金鑰認證）"
        log_warn "init 命令需要 SSH 金鑰認證才能自動執行"
        log_warn ""
        log_warn "請先配置 SSH 金鑰："
        log_warn "  ssh-copy-id ${user}@${host}"
        log_warn ""
        log_warn "或手動在 Pi 上執行初始化步驟（見 README 九、步驟 3-8）"
        return 1
    fi

    log_info "在 Pi 上建立環境 ..."
    ssh "${user}@${host}" bash </dev/null <<REMOTE_INIT
set -e

# 建立系統用戶
sudo useradd -r -s /bin/false -d ${SERVER_DEPLOY_PATH} sdprs 2>/dev/null || true

# 安裝系統依賴
export DEBIAN_FRONTEND=noninteractive
sudo -E apt-get update -qq
sudo -E apt-get install -y -qq python3-full python3-venv python3-dev portaudio19-dev ffmpeg autossh > /dev/null

# 修復 piwheels 連線問題
if [ -f /etc/pip.conf ]; then
    sudo mv /etc/pip.conf /etc/pip.conf.bak
fi

# 建立 venv
python3 -m venv ${EDGE_VENV}
${EDGE_VENV}/bin/pip install --upgrade pip -q
${EDGE_VENV}/bin/pip install -r ${SERVER_DEPLOY_PATH}/edge_glass/requirements.txt --prefer-binary -q

# 建立事件目錄
mkdir -p ${SERVER_DEPLOY_PATH}/edge_glass/events

# 安裝 systemd 服務
sudo cp ${SERVER_DEPLOY_PATH}/edge_glass/systemd/sdprs-edge.service /etc/systemd/system/
sudo cp ${SERVER_DEPLOY_PATH}/edge_glass/systemd/autossh-tunnel.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable sdprs-edge

# 設定權限
sudo chown -R sdprs:sdprs ${SERVER_DEPLOY_PATH}

echo "[OK] 邊緣節點初始化完成"
REMOTE_INIT

    echo ""
    log_info "下一步:"
    log_info "  1. ssh ${user}@${host} 'sudo nano ${SERVER_DEPLOY_PATH}/edge_glass/config.yaml'"
    log_info "     - 修改 server.api_url 為中央伺服器 IP"
    log_info "     - 修改 server.api_key 與伺服器 .env 的 EDGE_API_KEY 一致"
    log_info "     - 修改 server.mqtt_broker 為中央伺服器 IP"
    log_info "  2. 配置 SSH 金鑰用於反向隧道（見 README 九、步驟 5）"
    log_info "  3. ssh ${user}@${host} 'sudo systemctl start sdprs-edge'"
    echo ""
}

# ===== 部署目標 =====

deploy_server() {
    local host="${SERVER_HOST}"
    local user="${SSH_USER}"

    echo ""
    log_info "========== 部署中央伺服器 (${host}) =========="
    echo ""

    check_ssh_connection "$host" "$user" || return 1
    sync_to_host "$host" "$user" "$SERVER_DEPLOY_PATH" "${SERVER_EXCLUDES[@]}"

    if [ "${DRY_RUN:-false}" != "true" ]; then
        # 更新 Python 依賴（如果 requirements.txt 有變更）
        log_info "檢查 Python 依賴更新 ..."
        ssh "${user}@${host}" \
            "if [ -f '${SERVER_VENV}/bin/pip' ]; then \
                 ${SERVER_VENV}/bin/pip install -q -r ${SERVER_DEPLOY_PATH}/central_server/requirements.txt --prefer-binary 2>/dev/null; \
             else \
                 echo 'venv 不存在，請先執行: deploy_sync.sh init-server'; \
             fi" </dev/null || log_warn "依賴更新跳過"

        # 複製最新的 service 檔案並重啟
        ssh "${user}@${host}" \
            "sudo cp ${SERVER_DEPLOY_PATH}/central_server/systemd/sdprs-server.service /etc/systemd/system/" </dev/null || true

        restart_service "$host" "$user" "sdprs-server"
    fi

    echo ""
    log_ok "中央伺服器部署完成!"
}

deploy_glass() {
    local node_num="$1"
    local node_id="glass_node_${node_num}"
    local host="${SDPRS_GLASS_HOST:-sdprs-glass-${node_num}.local}"
    local user="${SSH_USER}"

    echo ""
    log_info "========== 部署邊緣節點 ${node_id} (${host}) =========="
    echo ""

    check_ssh_connection "$host" "$user" || return 1
    sync_to_host "$host" "$user" "$SERVER_DEPLOY_PATH" "${EDGE_EXCLUDES[@]}"

    if [ "${DRY_RUN:-false}" != "true" ]; then
        # 更新 Python 依賴
        log_info "檢查 Python 依賴更新 ..."
        ssh "${user}@${host}" \
            "if [ -f '${EDGE_VENV}/bin/pip' ]; then \
                 ${EDGE_VENV}/bin/pip install -q -r ${SERVER_DEPLOY_PATH}/edge_glass/requirements.txt --prefer-binary 2>/dev/null; \
             else \
                 echo 'venv 不存在，請先執行: deploy_sync.sh init-glass ${node_num}'; \
             fi" </dev/null || log_warn "依賴更新跳過"

        # 複製最新的 service 檔案
        ssh "${user}@${host}" \
            "sudo cp ${SERVER_DEPLOY_PATH}/edge_glass/systemd/sdprs-edge.service /etc/systemd/system/ && \
             sudo cp ${SERVER_DEPLOY_PATH}/edge_glass/systemd/autossh-tunnel.service /etc/systemd/system/" </dev/null || true

        restart_service "$host" "$user" "sdprs-edge"
    fi

    echo ""
    log_ok "邊緣節點 ${node_id} 部署完成!"
}

deploy_all() {
    local failed=0
    local glass_nodes="${SDPRS_GLASS_NODES:-01}"

    # 部署伺服器
    deploy_server || ((failed++))

    # 部署所有邊緣節點
    IFS=',' read -ra NODES <<< "$glass_nodes"
    for node_num in "${NODES[@]}"; do
        node_num=$(echo "$node_num" | tr -d ' ')
        deploy_glass "$node_num" || ((failed++))
    done

    echo ""
    echo "========================================"
    if [ "$failed" -eq 0 ]; then
        log_ok "所有節點部署成功!"
    else
        log_error "${failed} 個節點部署失敗"
        return 1
    fi
}

# ===== 主程式 =====

DRY_RUN="false"

# 解析參數
while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run|-n)
            DRY_RUN="true"
            shift
            ;;
        --help|-h)
            show_usage
            exit 0
            ;;
        init-server)
            init_server
            exit $?
            ;;
        init-glass)
            if [ -z "${2:-}" ]; then
                log_error "請指定節點編號，例: deploy_sync.sh init-glass 01"
                exit 1
            fi
            init_glass "$2"
            exit $?
            ;;
        server)
            deploy_server
            exit $?
            ;;
        glass)
            if [ -z "${2:-}" ]; then
                log_error "請指定節點編號，例: deploy_sync.sh glass 01"
                exit 1
            fi
            deploy_glass "$2"
            exit $?
            ;;
        all)
            deploy_all
            exit $?
            ;;
        *)
            log_error "未知命令: $1"
            show_usage
            exit 1
            ;;
    esac
done

# 沒有提供命令
show_usage
exit 1
