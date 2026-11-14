# 0.13.2 实际演示的 SQLite Test 阶段

已在 exact `59fa01ddc48fea0b605a8322478010b4adcff17a` 完成本机实际 Cocoa 录制，
36 阶段、492 帧、82 秒；逐阶段代表帧和首中尾联系表已审查，GIF 与原回执精确复制至 README。
这不代表已发布 0.13.2 安装器或完成三平台完整 UI 验收，详见[验证记录](validation-0.13.2.md)。

`--include-sqlite-test` 在全部既有工作流之后、Oracle 表单之前增加一个阶段
`sqlite_connection_test`。全部扩展选项合用时从 35 阶段变为 36 阶段，未启用此选项时
原有阶段及顺序不变。新阶段保持约三秒，只在实际 Test 完成、专用线程池空闲、独立
只读核查确认两行生成数据和文件摘要未变后开始抓取。

```bash
.venv/bin/python scripts/record-demo.py \
  --output artifacts/readme-0.13.2/opennavicat-demo.gif \
  --platform cocoa --theme dark --width 1440 --height 900 --fps 6 \
  --include-mongodb --include-stream --include-sqlite-test \
  --include-oracle-form --include-connection-chooser
```

新建、未保存的 SQLite 表单真实显示并使用 `generated-connection-test.sqlite`。文件由
I/O worker 在本次独立 TemporaryDirectory 中生成；录制子进程在首次提交 I/O 任务前
切换至此目录，保留相对 PATH 工具的原解析位置。输出与编码器路径预先转为绝对路径。
成功或失败均先排空 I/O 队列，再恢复 CWD/PATH，最后删除临时目录；不在源码 CWD 写入
这个数据库，也不在测试后更换文件框文字来掩盖路径。

阶段使用应用实际 Test 按钮、原生 SQLite 会话和后台 probe。每个临时会话的配置与关闭
由包装器核对，probe 返回值和完成信号均不伪造。公开 JSON 的 `sqlite_connection_test`
包含实际 SQLite 版本、后台完成/关闭、成功连线、原文件摘要和生成行数等事实；临时路径、
线程/PID、凭据和内部状态不会进入该段。表单只取消，不保存或访问已有 profile/keyring，
不点击复制按钮。失败清理先等待其独立测试任务与线程池结束，再销毁拥有任务信号的表单。

每帧仍通过既有可见文本保护；相对文件名不豁免路径、端点、密码或其它隐私检查。
实际录制后须独立解码、检查全部阶段的画面与隐私，再替换 README 中唯一的实际应用 GIF。
