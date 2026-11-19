# 新增 Skill 指南

本仓库（tyk_agent_skills）是 ZCode **插件市场**仓库：根目录 `marketplace.json` 是市场清单，每个 skill 是根目录下的一个插件文件夹。

别人只需要添加本仓库一个链接，安装里面任意 skill；新 skill 推送后，别人刷新市场即可看到。

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
git push          # 推到 origin（tyk_agent_skills 集合仓库）
```

推完别人在 ZCode 里：**插件市场 → 市场源 → 刷新 tyk-agent-skills**，即可安装新 skill。

### 5.（可选）同步到独立镜像仓库

若该 skill 也想要独立 GitHub 仓库（如 dscope-la-osc-skill），先在 GitHub 建空仓库 `Taoyukai/<skill-name>-skill`，然后：

```bash
git remote add <skill-name> https://github.com/Taoyukai/<skill-name>-skill.git
git subtree push --prefix=<skill-name> <skill-name> main
```

## 为什么不用 git submodule 做子仓库

别人添加市场时 ZCode 做的是普通 `git clone`，**不会拉取 submodule 内容**，skill 文件夹会是空的，安装直接失败。所以 skill 必须以实体文件放在本仓库；独立仓库只作为 subtree 单向镜像。

## 同步 dscope-la-osc 到其独立仓库

- **现在**（本仓库只有这一个 skill，两边内容一致）：

  ```bash
  git push skill main
  ```

- **以后**（本仓库有多个 skill 后，只推 dscope-la-osc 子目录）：

  ```bash
  # 首次切换需 force 一次，对齐历史（会用 subtree split 的干净历史覆盖镜像仓库）
  git push skill --force "$(git subtree split --prefix=dscope-la-osc main)":refs/heads/main

  # 之后日常同步
  git subtree push --prefix=dscope-la-osc skill main
  ```
