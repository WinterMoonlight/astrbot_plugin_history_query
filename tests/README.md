# 自动化验证

运行：

```powershell
python -m unittest discover -s tests -t . -v
```

常规用例只使用标准库。测试在受限的 .test-artifacts 临时子目录创建合成 SQLite 数据，关闭连接后清理，不读取用户聊天库，不连接网络或模型。

覆盖范围包括可信身份、跨平台同号、配置错误、权限撤销、不透明引用、并发队列与取消、累计预算、只读 SQL、隔离群会话、稳定游标、私聊多对话、正文变化、未知时间、工作进程复用/回收、工具权限代理和插件 hook 接入。

host_stub.py 是宿主契约替身，不是完整 AstrBot。可选 check_upstream_contract.py 会从指定的官方源码目录加载两个经 Git blob 校验的原始文件里的工具类，使用真正的 Pydantic 类构造和权限代理；仍不会启动真实宿主。

benchmark.py 为合成数据工作进程测试，输出的内存和耗时不包括真实宿主、NapCat 或模型。
