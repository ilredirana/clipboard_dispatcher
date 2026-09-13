# Tasker 使用说明

本目录的 XML 是任务模板。请从管理页“同步设备”下载已写入服务地址、设备 ID 和设备 Token 的文件，再导入 Tasker。直接导入仓库中的模板会保留 `__CLIP_*__` 占位符，无法连接服务端。

## 导入完整项目

完整项目包含自动上传 Profile、上传任务和下载任务，适合日常使用。

1. 在管理页创建 Android 设备，或打开已有 Android 设备的“查看当前配置”。
2. 下载或扫描“完整 Tasker 项目”对应的 `Clipboard Dispatcher.prj.xml`。
3. 在 Tasker 底部打开“项目”，长按项目列表后选择“导入项目”。
4. 选择下载的 XML，确认“剪贴板变化” Profile 处于启用状态。
5. 重新签发 Token 或重命名设备后，从管理页重新下载并覆盖导入项目。

Android 10 及以上系统需要按 Tasker 提示授予剪贴板访问权限。图片同步需要 Tasker 5.15.12 或更高版本。

## 只导入单个任务

管理页也提供已配置的单任务文件：

1. `Upload Clipboard.tsk.xml` 用于上传当前文本或图片。
2. `Download Clipboard.tsk.xml` 用于下载最新文本或图片并写入剪贴板。

单任务不包含“剪贴板变化”自动上传 Profile。手动运行上传任务时，任务读取当前剪贴板；传入第一个任务参数时，任务将其视为图片文件路径或 `content://` URI。

## 同步行为

完整项目使用 `Clipboard Changed` 事件触发上传。事件提供文本或图片 URI 时，任务直接上传事件内容；两者均为空时，任务再使用 Tasker 的 `Get Clipboard` 动作读取剪贴板。

下载任务只请求一次 `/api/clipboard/download`。文本直接写入剪贴板；图片写入 `Tasker/.clipboard_dispatcher/cache/` 后再设为系统剪贴板内容。缓存目录含 `.nomedia`，不会进入系统图库，并且只保留当前图片。

任务已启用“忽略由 Tasker 设置的剪贴板”。下载任务写入剪贴板后不会触发回传。

## 限制与排查

服务端默认允许 PNG、JPEG 和 WebP 图片，单张图片上限为 5 MB，分辨率上限为 4 亿像素。服务端关闭图片同步或降低上限时，Tasker 会显示服务端返回的错误信息。

自动上传未触发时，依次检查：

1. “剪贴板变化” Profile 是否启用。
2. Tasker 是否获得剪贴板访问权限。
3. 设备是否启用，且尚未重新签发 Token。
4. 手动运行 `Upload Clipboard` 后显示的错误信息。

更多设备管理和网络排查见 [项目使用说明](../../docs/使用说明.zh-CN.md)。
