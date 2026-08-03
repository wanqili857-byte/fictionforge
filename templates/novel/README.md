# 接入一本新小说

框架与小说的唯一接口是 `novel_config.json`。换小说 = 换配置 + 换内容，框架代码零改动。

## 步骤

1. **复制模板**：把本目录复制为 `novels/<你的小说>/`
   ```bash
   cp -r templates/novel novels/你的小说名
   ```
2. **填 `novel_config.json`**
   - `title` / `protagonist`：小说名 + 主角的 cast key
   - `protagonist_pronoun`：主角人称代词（"她"/"他"…）。system prompt 硬约束防混用，必填
   - `cast`：谁参加引擎模拟
     - Tier1（戏份重）：自己写 agent，`impl` 指向 `agents/agent_xxx.py`
     - Tier2（配角）：`impl` 用 `framework.agent_lite`，不用写代码
   - `pov_labels`：引擎输出里 pov key 的显示名
   - `bible_files`：哪些 bible 文件注入 system prompt
   - `quality`：anti-AI 词、比喻上限、温情角色（verve 检查用）
     - `forbidden_words`：绝对禁止词（gen.py 质量检查 + LiteAgent system prompt）
     - `world_summary` / `world_core_principle`：蒸馏世界观（引擎世界模拟注入用）
     - `anomaly_words`：场景强度权重用的异常关键词（不配则用通用词表）
     - `perception_stages`：感知阶段顺序（Tier1 agent 的感知过滤参考）
3. **写 `bible/` 注入文件**（都有模板注释）
   - `写作法则.md` → 生成模型的写作技法
   - `世界观.md` → 世界规则（自动摘要）
   - `人设.md` / `人物锚点.md` → 人物（人设注入生成、人物锚点注入章节结构设计）
4. **写 Tier1 agent**（`agents/agent_主角.py`）
   - 继承 `framework.agent_base.Agent`
   - 实现 `from_vault_state(vault_state, vault_reader, memory_store)`
   - 参考接口：`framework.agent_base.Agent`（记忆 + 信念修正 + 弧末反思）
   - 配角免写——用 `framework.agent_lite`
5. **建 `vault/`**：角色状态 + 章节状态 + 时间线（参考示例小说的 vault 结构）
6. **写弧配置**（`arcs/*.md`，YAML frontmatter）
7. **跑**
   ```bash
   # 单章生成（spec → 正文 + 质量门禁）
   python scripts/gen.py novels/<你的小说>/specs/ch1.json

   # 框架单元测试（引擎数据流相关）
   python tests/test_engine_core.py
   ```

## 内容包放仓库外（私有连载）

内容包默认放 `novels/<你的小说>/`。但你的小说若是**私有不公开**（连载中、有版权），不要放这个 public 仓库里——放独立目录，spec 声明 `novel_dir` 指向它：

```json
{
  "novel": "你的小说名",
  "novel_dir": "..",
  "chapter": 1,
  ...
}
```

- `novel_dir` **相对路径以 spec 所在目录为锚**：`".."` = 内容包根目录（bible / vault / chapters 都在那里）
- 绝对路径也行：`"/Users/you/novels/灰色频段"`
- 没写 `novel_dir` 时照旧落回 `novels/<novel>/`
- spec 的 `output` 本来就是相对 spec 目录，正文自动写回内容包自己的 `chapters/`
- 跑法：`python scripts/gen.py ~/novels/你的小说/specs/ch1.json`

这样引擎代码公开、你的内容私有，两套 git 互不相干。

## 最低可用包

只想试 gen.py 单管线（不跑引擎），cast 可以留空或只留主角：
- `novel_config.json`：`title` + `protagonist` + `bible_files` 即可
- `bible/写作法则.md` + `bible/世界观.md` + `bible/人设.md` 三件套
- `vault/` 可最小化

## 检查清单

- [ ] `python scripts/gen.py novels/<你的小说>/specs/ch1.json --force` 能出正文
- [ ] `python tests/test_engine_core.py` / `python tests/test_split_scenes.py` 框架测试通过
- [ ] 输出章节无 framework 报错、bible 注入正常（看 `[bible] loaded` 日志）
