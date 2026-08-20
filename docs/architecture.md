# 架构说明

## 进程边界

- Renderer：React，仅能访问 preload 暴露的最小 API。
- Electron Main：窗口、文件选择、安全存储、自定义只读图片协议、worker 生命周期。
- Python Worker：SQLite、扫描、caption、导出、模型管理和训练子进程。
- ML/Trainer 环境：首次启动按需创建，避免 Caption 与 sd-scripts 的依赖相互污染。
- 打包 worker 不携带 Torch/ONNX；WD14/JoyCaption 通过 caption 环境中的批处理子进程执行，模型在每个阶段只加载一次，结束时释放 GPU。
- Trainer 引导器下载固定的 sd-scripts commit，启动前校验 commit、`--help` 参数和两步显存分配探测。

Renderer 不直接读取本地文件，也不能直接启动进程。所有请求均经过 `packages/contracts` 中的 RPC v1 契约。

## 项目目录

```text
example.alora/
├── project.json
├── project.sqlite3
├── thumbnails/
├── exports/
│   └── <timestamp-project-id>/
│       ├── images/
│       ├── dataset.toml
│       └── manifest.json
└── training-runs/
```

源图片路径只记录在数据库和 manifest 中，任何阶段都不向源目录写文件。

## Caption 数据流

```text
Pixiv metadata ─┐
WD14 scores ────┼─> LLM JSON sections ─> deterministic validator ─> caption revision
JoyCaption ─────┘
```

确定性校验器负责排序、大小写、下划线、去重、trigger 和 `@artist`；LLM 无权直接写最终 sidecar。

## 任务状态

任务状态为 `queued → running → succeeded/failed/cancelled`。训练额外支持 `pause_requested → paused`。GPU executor 只有一个 worker，避免 WD14、JoyCaption 和训练同时占用显存。
