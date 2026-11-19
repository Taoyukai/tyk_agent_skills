# 新增 Skill 指南

本仓库（tyk_agent_skills）是**唯一**的 skill 仓库，也是 ZCode **插件市场**：根目录 `marketplace.json` 是市场清单，每个 skill 是根目录下的一个插件文件夹。

别人只需添加本仓库一个链接，即可安装里面任意 skill；新 skill 推送后，别人刷新市场即可看到。所有 skill 都以文件夹形式放在本仓库，不单独建仓库。

> **源码规则**：开发源码一律放各 skill 的 `source/` 目录（已被 `.gitignore` 忽略），保留本地、永不上传。

## 新增一个 skill

### 1. 建目录

在仓库根目录新建 `<skill-name>/`，结构仿照 `dscope-la-osc/`：

```text
<skill-name>/
├── .zcode-plugin/
│   └── plugin.json          # 插件清单
├── skills/
│   └── <skill-name>/
│       ├── SKILL.md         # 技能主文件
│       └── assets/          # 技能所需资料（可选）
└── source/                  # 开发源码，仅本地（见第 3 步）
```

`plugin.json` 模板（name 与目录名必须一致）：

```json
{
  "name": "<skill-name>",
  "version": "0.1.0",
  "description": "...",
  "author": { "name": "Taoyukai" },
  "license": "MIT",
  "skills": "skills"
}
```

### 2. 注册到市场清单

在 `marketplace.json` 的 `plugins` 数组追加一个条目（仿照现有 dscope-la-osc 条目）：

```json
{
  "name": "<skill-name>",
  "source": "./<skill-name>",
  "version": "0.1.0",
  "description": "...",
  "displayName": "<skill-name>",
  "author": { "name": "Taoyukai" },
  "category": "developer-tools",
  "componentTypes": ["skill"]
}
```

### 3. 忽略本地源码

`.gitignore` 追加一行：`<skill-name>/source/`

### 4. 提交并推送

```bash
git add . && git commit -m "add <skill-name> skill"
git push
```

推完别人在 ZCode 里：**插件市场 → 市场源 → 刷新 tyk-agent-skills**，即可安装新 skill。

## 为什么不用子仓库（git submodule）

别人添加市场时 ZCode 做的是普通 `git clone`，**不会拉取 submodule 内容**，skill 文件夹会是空的，安装直接失败。所以每个 skill 必须以实体文件夹放在本仓库，不再单独维护 skill 仓库。
