# DASE7506 MP3 — SQL Repair Agent

Mini-project 3 的独立项目仓库，采用确定性的 SQL 修复与工具反馈循环。

## 已验证结果

- 官方本地 test：**195 / 240，81.25%**；每题通过全部三个评分实例才算正确。
- 纯 CPU，四个 worker；预测加评分实测约 **147.34 秒**（Windows）。
- 最终评测不调用语言模型或外部 API，不加载模型/LoRA 权重。
- Validation：完整方案 106/120；移除实体名修复 105/120；仅提示修复 83/120；原始草稿基线 47/120。

## 文件

- [实现与复现说明](submission/README.md)
- [方法报告 PDF](submission/report.pdf)
- [官方完整评分 JSON](submission/outputs/test-score.json)
- [预测与工具轨迹](submission/outputs/test.json)
- [运行资源记录](submission/outputs/test-runtime.json)
- [验证与消融实验](submission/experiments/)
- [固定版本提交包](artifacts/MP3_SQL_195of240_CPU.zip)

提交包 SHA256：`cf0500857f26031d9094db663929fca1c6ce13618ad6c422fd6462794b172510`。

## 复现

使用 Python 3.11 或 3.12，官方 student package 需自行下载；本仓库不包含数据库或官方评测实现。

```bash
python -m pip install -r submission/requirements.txt
python submission/reproduce.py --official "/path/to/MP3_SQL_student_package" --split test
```

复现会重新生成 `submission/outputs/` 下的评分文件。详细命令、依赖、消融配置、原始结果和数据来源见 submission 内的说明与报告。

## 状态与说明

这些是官方本地评测结果。上传仓库不代表已完成课程页面或 GitHub issue 的成绩提交，最终等级由截止后的有效排名决定。

OpenAI Codex 协助实现、调试、实验和报告整理；外部 AI 使用与探索性的训练实验已在报告中披露。最终部署的 CPU 方案不包含训练权重。
