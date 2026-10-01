# CanonBench 复现协议（v0，随 v2 跑批生效）

> 目标：**任何人照本文能在自己机器上复现榜单上的一行数字**，并且知道每一环
> 哪些是确定的、哪些不是。复现不了的基准不是基准。
>
> 配套：`docs/BENCH_PLAN.md`（实施计划）、`docs/canonbench-writeup.md`（方法文章）、
> `bench/contracts.py`（契约，RunManifest 是可追责的最小单位）。

---

## 0. 三个前提，先说清

1. **生成不是确定的**：同一 prompt 同一模型跑两次，正文不会逐字相同（temperature=0.85）。
   能逐字节复现的是**输入**（宇宙、prompt、hash）与**判定**（机械判定器），不是生成正文。
   所以协议要求 k 次重复，且归因结论只在 k≥3 时才可下。
2. **模型 id 带日期后缀**（如 `doubao-seed-2-1-pro-260915`）——这是本基准选方舟通道的
   理由之一：供应商静默换权重是榜单杀手，日期后缀至少把"用的哪个快照"钉住。
3. **判定器版本必须固定**：判决可重算（`--rejudge`），但不同判定器版本的数字不可比。
   榜单每一行都带 judge 版本号，跨版本比较要全量重判。

## 1. 环境

```bash
git clone https://github.com/wanqili857-byte/fictionforge.git
cd fictionforge && git checkout feat/canonbench   # 榜单行注明 commit hash
python3 --version   # 3.9+ 均可；判定器零第三方依赖
pip install requests
```

零网络可跑的部分：全部单元测试、宇宙生成、`--rejudge`（对已有正文重判）、报表。

## 2. 宇宙校验（确定性 ground truth）

```bash
python3 - <<'EOF'
import sys, tempfile, hashlib; sys.path.insert(0, '.')
from pathlib import Path
from bench.universe.generator import generate, write_universe
u = generate(seed=42, chapters=6)
d = Path(tempfile.mkdtemp()); write_universe(u, str(d))
print(hashlib.sha256((d/'universe.json').read_bytes()).hexdigest())
EOF
# 期望: 34cc153008c9ca84…（generator version u0.1.0）
```

生成器零 LLM、纯确定性，同 seed 逐字节相同——这是判定的地基。不一致 = 你的代码
不是跑榜那一份。

| seed=42, chapters=6 | sha256（前 16 位） |
|---|---|
| `universe.json` | `34cc153008c9ca84` |
| `novel_config.json` | `a3e3b45192205c68` |
| `bible/真相表.json` | `bdbf0087774c40d7` |

## 3. 配置生成通道

key 放 `~/.env`（项目外，不会提交）。变量名按通道：

| 通道 | 变量 | 直连性 |
|---|---|---|
| `ark`（火山方舟 coding plan） | `ARK_API_KEY` | 国内直连 |
| `deepseek` | `DEEPSEEK_API_KEY` | 国内直连 |
| `openrouter` | `OPENROUTER_API_KEY` | 需代理出海（**已停用**，2026-09 额度耗尽） |

代理是复现的头号坑：`requests` 默认继承 macOS 系统代理，会把国内域名也劫走
（实测方舟经代理握手 EOF，而 curl 不读系统代理、能通——坑只在代码路径出现）。
框架已显式处理（`bench/runner/llm.py: resolve_proxy`），换机器可用 `CANONBENCH_PROXY`
覆盖：`CANONBENCH_PROXY=direct` 全直连，或 `CANONBENCH_PROXY=http://host:port` 指定代理。

```bash
python3 -m bench.runner.batch --list-models   # 看目录与通道状态，不花 token
```

## 4. 跑批

```bash
# 单模型三档（先小后大，验证通了再扩矩阵）
python3 -m bench.runner.batch --seed 42 --chapters 6 \
    --models ark-db-lite --tiers bare,mid,full --k 1 --out runs/mine

# 榜单级矩阵（一个厂商一个模型 × 三档 × k≥3）
python3 -m bench.runner.batch --seed 42 --chapters 6 \
    --models ark-db-lite,ark-glm-flash,ark-ds-flash \
    --tiers bare,mid,full --k 3 --out runs/mine
```

- **断点续跑**：章节文件已存在即跳过（`usage.jsonl` 保住 token 账），中断重跑不丢结果。
- **额度墙**：通道不可用在起跑前报错（`models.py CHANNELS`），不烧到一半才撞。
- **自接模型**：给 `bench/runner/models.py` 加一条 `ModelSpec`（provider 已有的话只加
  别名），或 PR。成本口径见 §6。

## 5. 重判与报表

```bash
# 判定器升级后：只重判不重生成（零 LLM 调用）
python3 -m bench.runner.batch --seed 42 --chapters 6 \
    --models ark-db-lite --tiers bare,mid,full --k 1 --out runs/mine --rejudge

# 报表（纯离线）
python3 -m bench.report.metrics runs/mine --write   # → runs/mine/report.md
```

## 6. 提交格式

一个目录，结构必须是：

```
<run>/
  ch{n}.md          生成正文（判定对象，逐章原样）
  ch{n}.fixed.md    仅 full 档：门禁修正稿
  run.json          RunManifest（契约见 bench/contracts.py）
  usage.jsonl       逐章 token/cost 账
  violations.json   修前/修后违反清单
results.json        矩阵汇总
report.md           报表（可选，可由上两项重算）
```

验收方（或 CI）逐项检查：

| 检查点 | 通过条件 |
|---|---|
| 宇宙一致性 | manifest 里 `bench.universe_seed` + generator version 与榜单行一致 |
| prompt 可追责 | `prompt_hashes` 每章一条，重算 sha256(system+user) 逐章吻合 |
| 完成度 | `chapters_done == expected`；0 章或部分完成的行**不进榜** |
| 判决可重算 | 对 `ch*.md` 跑同版本判定器，`violations.json` 必须复现 |
| 成本口径 | `billing=subscription` 的行成本列标「订阅」，不与按量行比钱数 |
| 通道与代理 | `notes` 带 `billing=…; proxy=…`；代理来源不同不影响判定、只影响失败模式 |

不接受：正文截图、人工摘抄的数字、无 manifest 的裸结果。

## 7. 已知的不可复现项（诚实清单）

- **生成正文本身**（采样波动；k≥3 + 置信区间是唯一缓解）
- **供应商侧的静默变更**：同 id 换权重、路由策略变化；日期后缀只是缓解不是免疫
- **订阅额度**：方舟 coding plan 额度耗尽表现为跑批中断，已生成章节保留、可续跑
- **上下文尾部截断**：`prior_tail=1200` 字符，正文超长时后续章只看尾部——参数在
  `bench/runner/batch.py` 顶部，改了要与榜单行声明一致

## 8. 榜单准入

1. 完整运行（§6 完成度）
2. 判定器版本与榜单当前版本一致（不一致则全量重判后再交）
3. k≥3 才可标"归因"（三档差值）；k=1 的行只能看方向
4. 核心指标 = 剔篇幅后的核心违反（密度 + 绝对数成对）；篇幅单独报
5. judge 与被评模型异家族（防自我偏好）；LLM 判定层上线后另加 Kappa 准入
