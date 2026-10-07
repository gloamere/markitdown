# 分支、上游同步与发布

## 当前状态

- 上游：`https://github.com/microsoft/markitdown`
- 二开仓库：`https://github.com/gloamere/markitdown`
- 首次同步日期：2026-10-05（UTC）
- 首次上游基线：`4cc9fa17653d695d64fb9eee5b33d4de55ff84e8`
- 基线提交：`Restore JSON regression coverage for late non-ASCII text (#2601)`
- `main` 当前保留这份上游基线；它还不是已验收的 Web 产品，也没有进行线上部署
- 多用户 Web MVP 0.2 在 `dev` 开发，包含邀请注册、按用户隔离的持久化队列和限额；通过草稿 PR 供审阅，尚未批准合并到 `main`

## 约定

1. `main`：生产发布分支，只接收经过测试、审阅且明确批准的版本
2. `dev`：二开集成分支，先合并最新 `upstream/main`，在此开发和验证
3. 保留上游提交历史与 MIT 声明，Web 功能在独立 `packages/markitdown-web` 中维护，尽量降低上游合并冲突
4. 不把实验提交直接推到 `main`，不自动合并、不自动部署、不强制推送

以上是工作流约定，**不是已配置的 GitHub 分支保护或强制检查规则**。这次没有改变仓库安全/权限设置。

## 同步上游到 dev

先确认工作区无未提交文件，且 `origin` 指向自己的 fork、`upstream` 指向 Microsoft：

```bash
git status --short
git remote -v
git switch dev
git fetch upstream main
git merge --no-edit upstream/main
# 若有冲突：解决冲突，检查 diff，重新运行所有受影响的测试后再提交
bash scripts/check-web.sh
git push origin dev
```

记录 `git rev-parse upstream/main` 与本次测试结果。不要用 `reset --hard` 或 force push 抹去二开工作。

## 将来发布到 main

1. 提交 `dev → main` PR，审查功能、安全边界和依赖变更
2. 运行 Web 检查、上游相关格式回归，并检查该提交的 CI 结果
3. 对实际目标系统进行人工验收；公开部署需复核身份与权限、配置 HTTPS 和全局请求限流、验证目标主机上的既有 OS 级解析隔离并确认数据保留策略（见 MULTIUSER-RELEASE.zh-CN.md）
4. 代码版本标签只指向已通过最终验收的 `dev` 提交；合并 `main` 和生产部署仍需各自明确批准

建 PR 只代表提供审阅入口，不代表已批准生产发布。仓库保留上游 CI；本次新增 Web 检查通过 `scripts/check-web.sh` 执行。

### 当前 CI 注意事项

早期开发检查点曾跳过远程 CI；当前 v1 检查点已运行 GitHub Actions，不再使用 `[skip ci]`。全部 Python 测试任务在导入前关闭遥测，并包含 Web、真实浏览器和生产隔离作业。各精确提交的实际结果及未完成检查见 [V1-VALIDATION.md](V1-VALIDATION.md)。历史凭证能力不代表当前工作流状态；没有静默增加权限或凭据。
