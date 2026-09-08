# Security Policy

## Reporting a Vulnerability

请勿公开提交漏洞 issue —— 直接私信仓库维护者或发送安全报告至仓库维护邮箱
（P0 发布前由维护者在仓库 About 页公开）。

报告内容建议包含：影响模块、复现步骤、影响范围。我们承诺 7 天内响应。

## 已知边界（设计内取舍）

- 本仓库**不含任何密钥/API Key**；用户本机运行 LLM/数据导入时自行管理凭证
  （如 `POKELAB_DB` 等仅环境变量，不进代码）。
- card-db 的 LLM 解析调用由用户在运行时注入（本地 Qwen3-4B 或云端 OpenAI 兼容端点），
  凭证只存在于用户环境。
