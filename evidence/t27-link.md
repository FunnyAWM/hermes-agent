# T-27 · PC 侧 KouriChat Link 设备链（2026-11-09）

目标：PC 能作为同一条设备链的成员——要么当主设备（安卓来连），要么当从设备（镜像手机）。
协议以 Android `kourichat-next` 的 `data/sync/` 为事实来源，本端是 1:1 端口，两端只能同时改。

## 实现

| 文件 | 改动 |
|---|---|
| `sync/`（新增包，22 个模块 / 2,461 行） | `mnemonic`（BIP39 24 词）、`keys`（HKDF 派生认证 / 记录 id / content key）、`crypto`（AES-256-GCM，AAD = `recordId\|hlc-canonical\|deviceId`）、`framing`（4 字节大端长度 + UTF-8 JSON，32 MiB 上限）、`protocol`（`hello → challenge → auth → auth_ok`，HMAC 证明覆盖 `kourichat-sync-v1\|<nc>\|<ns>`）、`clock`（混合逻辑时钟）、`records` / `state_store`（信封编解码、`.meta/state.json`）、`scope`（白名单）、`engine`（双向协商：先 `list` 换全量清单，再 `push` 传本机更新）、`client` / `server` / `identity`（TLS）、`manager`（配对、按需同步、定时同步）、`discovery`（mDNS）、`scheduler`、`qr`、`errors` |
| `sync/engine.py` | 覆盖前归档到 `.versions/`，新 HLC 胜出；没有 CRDT，也没有交互式冲突仲裁 |
| `sync/scope.py` | 白名单：`SOUL.md`、`memories/MEMORY.md`、`memories/USER.md`、`notes/**.md`、`skills/**.md`、`sessions/<id>/{meta.json,messages.jsonl}`；`AGENTS.md`、`.versions/`、`.meta/`、`sessions/current` 永不同步 |
| `hermes_cli/sync_cmd.py`、`hermes_cli/subcommands/sync.py` | `hermes sync create-chain / pair / serve / now / watch / status / leave / reset-chain`；`pair` 的地址可省略 |
| `hermes_cli/config_defaults_kourichat.py`、`hermes_cli/web_routers/kourichat_link.py`、`hermes_cli/web_server.py` | 配置默认值（周期同步、监听端口）与 REST `/api/kourichat-link/*`（status / pair / create-chain / serve / sync / settings / leave / reset） |
| `tui_gateway/contracts/sync.py`、`methods_sync.py`、`server.py` | RPC `sync.*`；`SyncPairResult.master_endpoint` 返回实际配对到的地址 |
| `apps/desktop/src/app/capabilities/link/`、`src/api/kourichat-link.ts` | Capabilities → Link 页：配对（只填 24 词）、状态与计数、主设备启停、周期同步与端口设置 |
| `apps/desktop/src/i18n/{en,types,zh,zh-hant}*` | Link 文案三语；配对对话框不在要求手输地址，只显示「会自动在局域网内查找主设备」 |

## 配对语义（对齐 Android `SyncManager.joinChain`）

- 无地址时用 mDNS（`_kourichat-sync._tcp.local.`，服务名 `KouriChat Next`）发现候选，
  **逐个握手**：能通过 challenge-response 的才是同一条链，别的链的主设备失败后换下一个；
  总超时 8 s（`_JOIN_DISCOVERY_TIMEOUT_MS`）。
- 加链时本机已有内容**一并上行**（`compute_outgoing`），不是只拉不推。
- 已存地址失联（手机换网络）时，同步回落为扫描局域网。

## 本轮修掉的三个真实缺陷

1. `sync_cmd._cmd_pair` 打印 `state.master_endpoint`，而 `SyncState` 没有该字段 → 配对成功后
   `AttributeError`。改为从 `manager.status().master_endpoint` 取，发现到的地址只回写 settings。
2. `sync/discovery.py` 的 `SERVICE_TYPE` 少了 `.local.` → zeroconf 0.153 抛
   `BadTypeInNameException`，而这个异常正好被「发现是可选的」的 `except` 吞掉：注册与浏览双双
   静默失效，无地址配对永远找不到主设备。改为 `_kourichat-sync._tcp.local.`，由
   `tests/sync/test_discovery.py` 钉住。
3. 发现回调只取 `info.addresses[0]` → 一条记录带多个地址（回环 + 局域网）时只会试一个；
   改为所有 IPv4 地址都进候选，由握手决定是不是同一条链。

## 测试

```
HERMES_PYTHON=.venv/Scripts/python.exe scripts/run_tests.sh \
  tests/sync tests/hermes_cli/test_kourichat_link_router.py \
  tests/hermes_cli/test_sync_sessions_bridge.py tests/tui_gateway/contracts
→ 10 files, 118 tests passed, 0 failed

cd apps/desktop && npx tsc --noEmit -p tsconfig.json          → 0 errors
npx vitest run src/app/capabilities/link/link-tab.test.tsx \
               src/app/capabilities/index.test.tsx            → 15 passed
python scripts/check                                          → 14 checks, ok
```

`tests/sync/test_golden_vectors.py` 把加密原语钉在 RFC 5869 / BIP39 与 Kotlin 测试套自带的向量上；
`test_channel.py` 覆盖信封、握手、协商与白名单；`test_discovery.py` 覆盖 mDNS 服务类型与多地址候选。

## 真实链路 E2E（两个 `HERMES_HOME`，本机 mDNS）

1. A（主设备）：`hermes sync create-chain` 抄下 24 词 → `hermes sync serve` 监听 27890 并注册 mDNS。
2. B（从设备）：`hermes sync pair --words "<24 词>"` **不填地址** → 8 s 内发现 A 并配对，
   打印实际 master 地址。
3. B 侧先写的 `notes/from-pc.md` 在配对时上行到 A；A 新增 `notes/from-master.md` 后 B `sync now`
   拉回。双向均以 HLC 较新者胜出。

## 限制

- 同 06 交互式冲突仲裁不做：覆盖前只归档到 `.versions/`。
- 同 07 密钥轮换不做：`generation` 固定为 1。
- LAN 发现依赖可选依赖 `zeroconf`（本地 venv 已装 0.153.0）；未安装或路由器拦组播时扫描只会空手
  而归，此时用 `hermes sync pair host:port` 手填地址。`pyproject.toml` 的 `[sync]` extra 尚未声明——
  `hermes pm lock` 需要 github.com 可达，本轮网络被代理拦住，网络恢复后补 extra 并重新锁。
- 真机（安卓）验收未做：本机无安卓设备，链路语义只用两个 PC home 交叉验证。
