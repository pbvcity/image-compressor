# 项目规则

- 默认用中文回复；根目录有 `CLAUDE.md` 时先阅读。
- 路径基于脚本位置或相对路径；不写死外层项目名称、绝对路径或素材批次名称。
- `input/` 放待压缩素材，可随时删除或更换；不得依赖某批素材永久存在，不改写原图。
- `compressed/` 只放结果图片，保留输入的相对目录结构和像素尺寸。
- 含透明像素保留 PNG，Alpha 逐像素不变；完全不透明转质量 90 的 JPG。
- 日志、报告、复核记录统一放 `reports/`，固定文件名，每次任务覆盖，不积累历史，不混入输入或输出。
- 使用方式和目录约定维护在 `README.md`，Python 依赖维护在 `requirements.txt`。
- 修改脚本后运行 `python -m unittest discover -s tests -v`；实际压缩后运行 `scripts/verify_results.py` 复核当前批次。
