# 五档关系词典：实现与验证

基准 main d287221c241fe88740ab2186a8ffa63676d90dcf（#16–#19 已合并）。版本 0.2.4，schema 15。主运行解析已从三档基础词组合改为五档查表；完整55词条和125格由同一数据源生成在 docs/RELATION_LEXICON.md。

## 机制和复杂度

三个原分值各自分档、一次查表；不增加底层维度、数据库字段、模型调用、人格词库或事件总线。词条含稳定ID、短称谓和释义。公开接口只输出称谓；注入同时包含释义和三维分档以保留矛盾态度。一次已获取的有效记录供注入复用，不再额外查询称谓。

分档仅用于表达，不影响评分、排名、预算、冷却或权限。中性T不等于无证据；词典不输出患难、背叛、修复等历史断言，不预设恋爱关系。最负边界-40保留既有戒备/疏远归属，其余边界约定见词典。

## 兼容与回滚

默认构造 RelationResolver() 使用新词典。已支持的显式 thresholds/labels 构造继续使用旧解析，避免静默吞掉调用方规则；thresholds={} 可用于旧版离线对照。没有新增宿主配置开关。历史超范围A仍只在显示时饱和。临时覆盖、撤销、重启沿原服务读取有效状态；不持久化称谓。回滚代码即可恢复旧显示，无schema迁移、历史重算或补评。

## 实际验证

- main基线：563 passed。
- python -m pytest -q --tb=short --basetemp=<会话目录>：590 passed，包含125格完整性、23项边界、混合态度、显式旧配置、临时覆盖/重启/撤销、重复显示不写流水。
- python -m black --check .：81文件通过；node --test tests/dashboard.test.cjs：19 passed；python scripts/check_project.py：0.2.4通过。
- python scripts/compare_relation_lexicon.py --replay <Phase2>/results.json --output <目录>：55词条、125格、52条轨迹。
- python scripts/replay_relations.py --arms current --output <空目录>：1201条；逐项比对旧结果的before/after/delta/changed/status/provider_called全部一致。PFUR仍6/381，受话人误更新0/8，Trust误更新2/1195，调用492。
- 固定轨迹旧/新称谓变化均13次；不能据此推断真实表达改善或没有其他阈值震荡。

独立AstrBot4.28.0装配及24条消息转换检查、HEAD归档及远端检查结果记录在PR。

## 尚未证明与后续

词条贴切度、模型是否遵循释义、真实人格体验未测。五档增加的阈值先作为表达契约；后续诊断只记录版本、实际状态和词条转换，不以翻转次数自动调参。没有历史滞回或Trust证据字段。已有6条语义误更新仍由单独归因实验处理，不能把更丰富的词语当作关系判断更准确。
