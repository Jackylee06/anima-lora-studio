# Anima LoRA Studio

面向 CircleStone Labs Anima Base v1.0 的 Windows 本地 LoRA 工作流：

`Pixiv 导入 → 只读筛选 → WD14 → JoyCaption → LLM refine → 审核 → 导出 → sd-scripts 训练 → 固定种子样图`

## 当前功能

- Electron + React 中文桌面界面，Python worker 使用版本化 NDJSON RPC。
- SQLite WAL 项目数据库；原始 Pixiv 目录只读，所有派生文件进入独立 `.alora` 项目。
- 支持完整 Pixiv 命名标记表，默认模板：

  ```text
  pixiv/{AI}/{age}/{user}-{user_id}/{id}-{title}
  ```

- 图片损坏、尺寸、比例、清晰度、感知哈希和近重复检查；人工保留/排除/待定。
- WD 标签稀疏向量的语义相似分组；图库同时标出近重复与语义相似组。
- WD EVA02-Large Tagger v3、JoyCaption Beta One NF4，以及 Ollama/OpenAI-compatible LLM refine。
- 确定性 Anima caption 组装：标签分区、顺序、下划线和 `@artist` 均经程序校验。
- 角色、画风、自定义 profile；caption revision 和批量审核。
- 自定义 profile 可复制角色或画风规则，再单独设置 tag/hybrid 模式与项目 refine 指令。
- 硬链接优先的可复现导出，附 `manifest.json` 和 `dataset.toml`。
- 固定 `kohya-ss/sd-scripts` commit `37a1cbbc5725ed2a3575506e7bd2001c9908ac92` 的 `anima_train_network.py`；安全预设、参数兼容性检查、两步显存探测、日志、保存点暂停和恢复。
- Windows 安装器、隔离 caption/trainer Python 环境引导、模型注册和 SHA-256 校验。
- Windows 安装版使用 GitHub Releases 检查更新，支持手动下载、进度、取消、重试、忽略版本以及重启安装；运行任务期间禁止安装更新。
- Caption/Trainer 环境固定使用 PyTorch 2.8.0 + Torchvision 0.23.0 的官方 CUDA 12.8 Windows wheel；后续依赖安装受同一 constraints 文件约束并在完成后验证 CUDA/BF16。
- Hugging Face 固定 revision 断点下载；Anima 权重下载前强制记录许可确认。

## 开发启动

要求：Node.js 20+、npm 10+、Python 3.11，以及用于扫描图片的 Pillow。

```powershell
npm install
npm run dev
```

桌面进程默认使用 `python` 启动 `services/worker/main.py`。也可指定：

```powershell
$env:ANIMA_WORKER_PYTHON = "C:\path\to\python.exe"
npm run dev
```

## 测试与构建

```powershell
npm test
npm run typecheck
npm run build
```

生成独立 worker 和 Windows NSIS 安装包：

```powershell
npm run package:win
```

产物写入 `release/`。构建脚本会在仓库的 `.runtime/build-worker` 中创建隔离环境并使用 PyInstaller；最终安装包运行核心界面与扫描功能时不依赖系统 Python。WD14、JoyCaption 和训练依赖由首次启动向导安装到 `%LOCALAPPDATA%\AnimaLoRAStudio\runtime`。

## 发布与应用更新

更新源为公开仓库 `Jackylee06/anima-lora-studio` 的稳定 GitHub Releases。推送与 `package.json` 版本一致的标签会在 Windows runner 上重新运行类型检查和全部测试，然后构建并发布 NSIS 安装包、blockmap 与 `latest.yml`：

```powershell
git tag v0.1.2
git push origin v0.1.2
```

开发模式不会访问更新源。已安装应用启动约 12 秒后静默检查，只有用户确认后才下载；下载完成后仍需点击“重启并安装”。正式分发前建议配置 Windows Authenticode 代码签名，避免 SmartScreen 警告并强化发布者身份校验。

## 推荐使用顺序

1. 新建项目，选择只读 Pixiv 根目录和独立项目父目录。
2. 扫描后在“图片筛选”中人工确认保留图片。
3. 在“模型与设置”安装 caption/trainer 环境；trainer 引导会下载固定 commit 的 sd-scripts，无需系统 Git、Python 或 Rust。
4. 依次运行 WD14、JoyCaption 和 LLM refine；未下载模型时可先用测试后端验证流程。
5. 审核 caption，并在“训练与评估”冻结训练集快照。
6. 注册 Anima Base、Qwen3、VAE 和 sd-scripts，确认模型许可，预览命令后启动训练。

## 模型与许可

- Anima LoRA 必须基于 `anima-base-v1.0.safetensors` 训练；Aesthetic/Turbo 仅作为兼容推理模型。
- 安全预设仅训练 DiT，通过 `--network_train_unet_only` 冻结 Qwen3，并且不启用 LLM adapter 训练。
- Anima 模型及衍生模型受 [CircleStone Labs 官方许可](https://huggingface.co/circlestone-labs/Anima)约束；软件会在首次训练前要求明确确认。
- WD14、JoyCaption、sd-scripts 和各模型权重保留各自许可证，本仓库不重新分发这些权重。

## 数据安全

- 扫描器不会在源目录创建 sidecar、缩略图或数据库。
- 云端 LLM refine 默认只发送 WD14 标签、JoyCaption 文本和规则，不发送图片。
- 导出使用新目录；同一 NTFS 卷优先硬链接，跨卷或硬链接失败时复制。
- API Key 由 Electron `safeStorage` 使用 Windows 系统能力加密。

## 实机验证边界

本仓库的 mock 全链路、打包 worker RPC、RTX 4090 Laptop GPU 识别和 Windows 桌面启动已经自动验证。真实 WD14、JoyCaption、20-step Anima 训练、checkpoint 恢复与样图生成需要本机先下载相应权重，未提供权重时测试套件不会伪装成真实推理成功。
