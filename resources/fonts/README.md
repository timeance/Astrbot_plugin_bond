# 排行中文字体

`BondSansSC-Regular.ttf` 由 Google Fonts 的 Noto Sans SC 提取 400 字重并保留 Unicode BMP 字符生成，随插件离线使用。原始版权及 SIL Open Font License 1.1 保留在 [OFL.txt](OFL.txt)，字体不按项目 AGPL 重新授权。修改字体重命名为 Bond Sans SC，未使用保留名称 Source。

- 来源：`google/fonts`，提交 `a85815a42757630ce188fdad368c2dfc444d4773`，`ofl/notosanssc/NotoSansSC[wght].ttf`。
- 原文件 Git blob：`fb0637bafbcd804fe32152370a1225990745b4bc`。
- 原文件 SHA-256：`a3041811a78c361b1de50f953c805e0244951c21c5bd412f7232ef0d899af0da`。
- 打包字体 SHA-256：`6791844c43c993047701f115a00b0e410857c72e90a2a07d84a795e4a8865549`。
- 覆盖 30445 个 BMP 字符；不保证罕见扩展汉字、彩色 Emoji 或所有 Unicode 文字。缺少头像时昵称首字作为占位。

制作使用 FontTools 4.58.5；它仅为资产制作工具，不是插件依赖。关键步骤如下（`source` 是上述校验后的原字体）：

```python
from fontTools.ttLib import TTFont
from fontTools.varLib.instancer import instantiateVariableFont
from fontTools import subset
font = TTFont(source, checkChecksums=2)
modified = font['head'].modified
font = instantiateVariableFont(font, {'wght': 400}, inplace=True)
options = subset.Options()
options.name_IDs = ['*']
options.name_languages = ['*']
worker = subset.Subsetter(options=options)
worker.populate(unicodes=range(65536))
worker.subset(font)
names = {1: 'Bond Sans SC', 2: 'Regular', 3: 'BondSansSC-Regular',
         4: 'Bond Sans SC Regular', 6: 'BondSansSC-Regular',
         16: 'Bond Sans SC', 17: 'Regular'}
for key, value in names.items():
    for platform, encoding, language in [(3, 1, 0x409), (1, 0, 0)]:
        font['name'].setName(value, key, platform, encoding, language)
font['head'].modified = modified
font.recalcTimestamp = False
font.save('BondSansSC-Regular.ttf')
```
