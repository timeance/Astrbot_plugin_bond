# 短论文配图

三张图由本目录的 [render_figures.py](render_figures.py) 生成，同时提供 PNG 与 SVG。SVG 将文字保存为路径，查看时不依赖本机中文字体；正文使用 PNG，并提供矢量版链接。

| 文件名 | 内容 | 来源 |
| --- | --- | --- |
| bond-dimensions | 三维含义与混合态度示意 | 当前规则 / 词典，非测量数据 |
| bond-pipeline | 平台事实、模型建议、本地更新和人格表达 | 当前监听器、评估、关系及注入服务 |
| bond-replay-results | 规则修改前后，在本应不变的测试中正确保持不变的比例 | Phase0/1/2 归档 JSON，读取位置见脚本 |

复现需要 Python、Matplotlib 和中文字体，均为**文档绘图依赖**，不加入插件运行依赖。可在已有绘图环境运行：

~~~bash
python docs/research/figures/render_figures.py --output <配图输出目录>
~~~

可用 `--font` 指定字体名称。未指定时依次查找 Microsoft YaHei、Noto Sans CJK SC、Source Han Sans SC、SimHei；找不到会报错，避免静默生成缺字图片。可通过 `MPLCONFIGDIR` 将字体缓存放入自己的临时目录。

对照图使用统一的 0–100% 尺度，并显示正确次数与分母。正确次数由“分母 − 原错误次数”得到；只说明本应不变的样本被正确处理的比例，不是包含正负全部情形的总准确率。脚本核对三阶段夹具相同、事件数相同和指标分母相同，主图对比原基线与 Phase2，中间阶段留在正文表格。它们代表各阶段归档，不是在当前入口下重新执行旧规则得到的结果。修改数据或机制后应一并更新正文、图注和快照说明。
