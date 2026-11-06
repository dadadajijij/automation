# 新东方问题类型与初筛规则

内部来源记录（不作为命中回复的默认附加说明）：用户提供的《新东方问题汇总.doc》，于 2026-10-27 整理；原件为 Confluence HTML 导出。本文保留其客户集成和处理分支，不把其中的【原因】当作本次现场根因。所有配置链接均为附件给定的内部入口，未在线核验其当前内容；本 Skill 只能给出建议，不能执行配置下发。

## 条件解释

- 先确认问题对象和平台；需要在线证据的分支再绑定实际 SID 和故障窗。仅依赖已明确平台、症状或使用场景的分支，不为凑证据强加在线查询。老师看学生画面旋转/模糊时检查学生发送端，不把老师接收端的数据当作学生数据；回声按候选回传源、原声端、听到回声端分开；只给一个“用户 ID”不能推定该用户就是噪声或回声源。
- 事件、Counter 都以查询成功且覆盖当前窗口为前提。查询失败、列表截断、SID 错误、Counter 未返回、null 或无有效点，均为证据不足。不得等价为“配置不存在”“值不高”“AEC 已开”或“网络正常”。
- 附件的默认规则是“相关事件完全未上报 → 条件不满足 → 通用支持”。Windows 摄像头分支显式规定“无 cameraInfo → 建议 MF + 720P”，这是特例；其他分支的“没有某配置”要求已返回可读取的相关 `vosdk.abtest`，但其中未含该键，不能由整个 Event 缺失推出。
- ABTest 配置判读只依据 `params` 中的目标键和值。`params` 为 JSON 字符串时先解析为对象，保留布尔、数字等真实类型；不要用其他事件元数据判断配置有效性或改变本矩阵分支。多个记录值冲突时，按真实 SID、时间与目标对象核对并披露冲突，不任选一条。
- “持续”按故障窗内有效点、时间覆盖和连续异常段判断，报告原始时间、min/max、有效点数及缺口；不是只看平均值或单个尖峰。附件没有给出持续时长、AEC Delay“波动较大”的定量阈值，不自行创造秒数或差值；证据不能支持持续/波动判断时，转通用支持。
- 现场新版本/平台/集成与附件不一致时，保留实际返回值与差异，转通用支持核验，不强套历史策略。
- 满足处理条件后按 [evidence-and-response.md](evidence-and-response.md) 直接给明确结论和处理方案，结论后单独注明“结论来自新东方专项SKILL。”。需要在线证据的分支先展示实际事件详情或 Counter 数据，再给结论、该分支推荐配置内容与入口、操作顺序、复测标准和已核验通话的 Argus 链接；不以“仅内部保留”省略关键证据。无需在线证据的分支简述已确认条件及来源，省略未执行的查询和空证据栏。不因未证实唯一根因而弱化已满足规则的建议，也不补写专项未支持的额外原因。
- 全部明确命中的分支统一省略来源/模板未核验、根因未确认、仅供参考和不能保证恢复等默认免责声明。各段中的因果和证据边界是内部判断约束，不逐条变成对外附加说明；通过准确的分支结论、处理方案和复测项落实。实际存在的配置/模板冲突、必要证据缺口和影响操作的限制仍需明确披露；用户追问根因或核验情况时据实回答。

## 视频问题

### 老师看学生画面旋转

先确认设备：

- 京东方小课屏 `e3/21a900`：设备自带旋转，建议先让用户在设备侧旋转。
- 其他设备：查询问题用户真实 SID 的 `vosdk.abtest`，检查是否已有 `che.video.camera_capture_rotation`。

判断：

- 已有该配置：列出已观察到的键、值及时间，不再重复推荐规避配置。
- 相关 `vosdk.abtest` 已返回但没有该键：给出候选配置 `che.video.camera_capture_rotation`：`1` 向左 90°、`2` 向左 180°、`3` 向左 270°。方向未知时列出对应关系或询问应旋转的方向，不能任选一个值。
- 整个 Event 缺失或无法确认：转通用支持。

参考配置入口：

- 单机向左 90°：`https://datalego.agoralab.co/reportdata/dispatch/54893`
- 单机向左 180°：`https://datalego.agoralab.co/reportdata/dispatch/80501`
- 单机向左 270°：`https://datalego.agoralab.co/reportdata/dispatch/80753`

### 学生画面模糊

查询问题用户的发送 SID，检查 Counter `87`，即 `Video High Send QP Quality Of Picture`：

- 持续高于 `40`：命中软编处理分支，建议 `{"che.hardware_encoding": 0}`，即切软编。
- `87` 有充分有效数据但不满足上述条件：按附件的另一明确分支，建议尝试单机切 Camera1。
- `87` 缺失或覆盖不足：不能按“不高”处理，转通用支持。

参考配置入口：

- 单机切软编：`https://datalego.agoralab.co/reportdata/dispatch/364510/info`
- 机型切软编：`https://datalego.agoralab.co/reportdata/dispatch/709752/info`
- 单机切 camera1：`https://datalego.agoralab.co/reportdata/dispatch/696331/info`

### Windows 摄像头采集失败、黑屏或绿屏

查询问题用户 SID 的 `vosdk.cameraInfo` Event：

- Event 存在且明确未枚举到摄像头：结论是“该 SID 的 cameraInfo 未枚举到摄像头”，停止该配置分支，不因此断言机器物理上没有摄像头。
- 查询成功但 Event 不存在，或 Event 存在且有摄像头枚举：建议切换 MF + 720P。这是附件对 Event 缺失的显式特例；查询失败不适用。
- 事件对象存在但载荷缺失、截断或无法读取枚举结果：转通用支持。

附件将第一个入口文字标成“单机切 camera1”，而处理方案写的是“切 MF”。这是原文命名冲突；保留链接和冲突说明，建议有权限操作人先核对该模板实际内容，不把 Camera1 与 Windows MF 说成同一种能力。

参考配置入口：

- 单机切 camera1：`https://datalego.agoralab.co/reportdata/dispatch/45908/info`
- 单机设置 720P 采集：`https://datalego.agoralab.co/reportdata/dispatch/292429/info`

## 音频问题

### 回声

先判断回声源是否为 PC：

1. 已确认的 PC 回声源：查询 Counter `634`，即 `A NEARIN APM STATUS`。使用公共 Skill 的同版本解码或返回的显式状态，不能把原始整数直接猜成 AEC on/off。
2. 如果明确 AEC 为 `off`：建议用户在 App 中打开回声消除，结束本分支。
3. 不是 PC，或 PC 的有效证据不满足 AEC off：查询 Counter `5`，即 `Audio AEC Delay`。缺少 PC `634` 时不推断 AEC 已开启；可补查 `5`，但需保留未核实的 AEC 状态。
4. 按附件顺序匹配：Android 且有效序列波动较大，建议关闭 OpenSL；否则有效值持续大于 `400`，建议把 AEC Filter 调到 `512`；否则在有效证据已排除前两条件时，建议 AEC 强度调到 `3`。同时满足时先给前序单项建议，不叠加成一组批量变更。
5. 以上相关配置已由现场配置或客户确认存在且生效，仍有回声：iOS/macOS 可建议单机 AINS；Android/Windows 附件说明默认未加载相关库，不建议该分支。不能因当前刚提出建议，就假设它已配置或复测失败。
6. 源 UID 未确认、指标缺失或无法判别：转通用支持。

参考配置入口：

- Android 关闭 OpenSL：`https://datalego.agoralab.co/reportdata/dispatch/50324`
- 单机 AEC Filter 512：`https://datalego.agoralab.co/reportdata/dispatch/50706/info`
- 单机 AEC 强度 3：`https://datalego.agoralab.co/reportdata/dispatch/292299/info`
- iOS/macOS 单机配置 AINS：`https://datalego.agoralab.co/reportdata/dispatch/161595/info`

### 杂音或噪声

- Windows 产生杂音：建议单机关闭 Boost。
- 其他平台或设备：不在本 Skill 中继续推荐配置，转给 `agora-wecom-support-assistant`；附件说明新东方已有全局降噪配置，不能直接重复套用高强度 NS。

参考配置入口：`https://datalego.agoralab.co/reportdata/dispatch/264103/info`

附件记载的全局高强度 NS 入口仅供核对现有配置：`https://datalego.agoralab.co/reportdata/dispatch/167991/info`。不能凭附件断言当前问题设备一定已命中该全局配置。

### 声音忽大忽小

确认问题对象是 PC，且主诉为音量忽大忽小时，按专项方案关闭 AGC：

```json
{"che.audio.enable.agc": false}
```

参考配置入口：`https://datalego.agoralab.co/reportdata/dispatch/124399/info`

移动端或平台未知不套用本分支，转通用支持。

### 声音小

先区分采集声音小还是播放声音小：

- 采集声音小：查询 `vosdk.abtest`，检查是否有全网 `che.audio.record.signal.volume: 150`。如果存在，建议把当前设备从全网配置排除，再单机配置采集 `300`。
- 播放声音小：建议单机播放 `300`。
- 无法区分采集/播放：先追问方向，不直接推荐配置。

采集分支未发现 `150` 或事件缺失时，转通用支持，不凭缺失直接建议采集 `300`。即使设备上报值为 `150`，也不单凭这个数值确认配置来源为全网；将“从全网排除”表述为需操作人核对目标设备命中和作用范围的建议。播放/采集 `300` 的准确参数键未在附件给出，不自行编造。

参考配置入口：

- 全网采集 150：`https://datalego.agoralab.co/reportdata/dispatch/140561/info`
- 单机采集 300：`https://datalego.agoralab.co/reportdata/dispatch/133607/info`
- 单机播放 300：`https://datalego.agoralab.co/reportdata/dispatch/128894/info`

### 无声或只有敲击屏幕声音

PC：查询 `vosdk.DeviceStatChange`，检查设备是否为虚拟声卡或虚拟麦克风：

- 虚拟设备：报告设备路由线索，继续结合后续支持流程。
- 非虚拟设备或 Event 没有相关字段：转给 `agora-wecom-support-assistant`。

移动端：

1. 查询 Counter `8`，即 `Audio Signal Level Nearout`。
2. 持续高于 `85`：该初筛规则不命中，转后续支持。
3. 持续低于 `75`：继续查询 Counter `251`，解析 `counter251 = audioProfile * 16 + audioScenario`。
4. 解出的 Scenario 数值为 `3/8`：按附件的客户处理规则建议切通话音量。
5. 解出的 Scenario 数值为 `0/1`：按附件的客户处理规则建议切媒体音量。
6. Counter `8` 在 `75~85`（含边界）、交替高低、null、无有效点或覆盖不足，以及 Scenario 为其他值/无法可靠解码：转通用支持，不填补附件未覆盖的区间。

参考配置入口：

- 单机切通话音量：`https://datalego.agoralab.co/reportdata/dispatch/49720/info`
- 机型切通话音量：`https://datalego.agoralab.co/reportdata/dispatch/46941/info`
- 单机切媒体音量：`https://datalego.agoralab.co/reportdata/dispatch/157513/info`

Counter 251 的详细解码规则见公共 Skill 的 `argus-online-channel-troubleshooting/references/audio-config-counter-251.md`。`251` 是 Counter ID，不是要代入公式的值；对实际返回值解码，含负数时按合法枚举反查，不能直接取模。附件把 `1` 称为 Chatroom，而公共参考注明它是旧/内部枚举、当前 Chatroom 为 `5`；`8` 也有历史命名差异。处理规则保留原数值 `0/1` 与 `3/8`，名称必须按现场 SDK 版本核实，不能擅自把 `1` 换成 `5` 或称其为当前通用 API 契约。

命中上述 Scenario 分支后直接给对应通话/媒体音量切换方案及入口，并要求同设备、同场景复测。

### 音画不同步

查询问题用户真实 SID：

- 检查上行网络是否不稳定、是否存在丢包；按实际媒体与方向查询音频 `p2s` 或视频 `p2sv`，需要双端时先核验真实 SID 重叠。
- 检查是否使用虚拟摄像头。

这两个条件只能作为初筛线索，不足以单独确认根因；查询后转后续支持完成完整音视频链路判断。

### 播放课件音频无声或时长为 0或者播放音频进度条不动

查询问题用户 SID 的 `vosdk.abtest`：

- 已返回的 `vosdk.abtest` 中有 `che.audio.use_ffmpeg_audio_decoder: false`（JSON 布尔值）：建议操作人核实全局配置命中，将该设备排除后单机配置 true。
- Event 可读取且完全没有 `che.audio.use_ffmpeg_audio_decoder` 键：建议单机配置 false。
- 已是 true、值类型/作用范围不明、整个 Event 缺失或查询失败：转通用支持。不能把“不是 false”一概当成“没有该键”。

先确认是课件在线 MP3 播放无声、时长为 0 或进度条不动，不把普通麦克风无声或对端订阅异常混入。命中后直接给对应 FFmpeg 配置方案；调整后用同设备、同音频复测，确认时长正常、进度条推进且有声音。

参考配置入口：

- 全局 FFmpeg false：`https://datalego.agoralab.co/reportdata/dispatch/186023/info`
- 单机 FFmpeg true：`https://datalego.agoralab.co/reportdata/dispatch/327317/info`
- 单机 FFmpeg false：`https://datalego.agoralab.co/reportdata/dispatch/69587/info`

### iOS 使用 AirPlay 但希望从手机本机出声

这是用户期望调整，不是故障判定。确认设备为 iOS 且正在使用 AirPlay 后，建议配置本机出声：

`https://datalego.agoralab.co/reportdata/dispatch/177522/info`

## 网络问题

用户频繁断线重连或完全无法连接 Agora：

- 初筛为网络异常候选。
- 建议切换 TCP Proxy：`https://datalego.agoralab.co/reportdata/dispatch/327179/info`
- 不在本 Skill 中执行配置；继续由后续支持流程确认网络、代理和 SDK 状态。

## 统一边界

- 默认按本文件“条件解释”处理 Event 缺失；保留 Windows cameraInfo 的附件明确例外，不扩大到其他类型。
- 配置建议链接只能作为建议，不代表已经下发、已经生效或已经解决问题。
- 未匹配类型、匹配但条件不满足，或缺少当前分支必需的身份/时间/SID 时，转 `agora-wecom-support-assistant`。无需在线证据的分支不因缺少无关 SID 或时间而阻塞。
- 来源标记仅用于实际命中的专项结论；证据不足、条件未命中和通用支持的独立结论均不使用。已命中分支直接交付证据（如需）、结论与方案，不再重复生成另一套同类建议；用户另有独立问题时继续处理，不能遗漏。

### 内部判断约束（不默认追加到命中回复）

- QP 高只能用于匹配软编处理条件，不自行写成已确认硬编码故障；PC 音量波动可匹配关闭 AGC 策略，不凭主诉确认 AGC 为根因；Windows 杂音分支不编造 Boost 已开启。
- Scenario 分支用于选择专项音量切换方案，不单凭它断言当前系统音量类型；用户追问当前类型时，按平台、Counter `251/134/15` 和同版本映射取证。
- FFmpeg 分支不从配置值推定下载失败。附件的在线文件请求异常方向仅在用户追问或调查需要时结合实际请求/日志核验，不作为已命中回复的固定附加说明。
- 不编造网络根因、链接核验结果、配置执行状态或复测成功；通过准确结论、具体步骤和复测项体现这些边界，不追加泛化免责声明。

## 受理示例

### 课程卡顿与老师上行丢包

输入：课程 ID `624637205`、老师 ID `29456985`、学员 ID `30929423`、直播时间 `2026/10/23 19:50-2026/10/23 21:00`，学员反馈卡顿，询问是否受老师丢包影响。

将 `624637205` 原样作为 `channelName`，老师和学员分别映射为两个精确 UID，时区默认上海。普通卡顿没有本矩阵的专项规避分支，应带这份受理记录进入通用支持，再调用 Argus 先定位通话、用户 SID 和真实在线交集。客户的“是不是老师丢包”只是待验证假设；不直接推荐 TCP Proxy、不把学生侧卡顿归给老师。媒体类型无法确定且影响下一步时再问声音、画面或两者。

### 课件音频进度条不动

输入：教室 ID `627708069`、用户 ID `31002068`，播放音频进度条不动。

把教室 ID 作为精确频道名候选、用户 ID 作为问题用户 UID。未给日期时先使用公共 Argus Skill 的时间兜底，不要求重复填写所有字段。确认是课件音频播放问题后，按实际 SID 查询 `vosdk.abtest`，分别处理 false、键缺失、true、Event 缺失和查询失败；不把进度条不动直接定性为网络下载失败。

### 老师看学生旋转且已有配置

学生发送 SID 的相关 `vosdk.abtest` 已返回 `che.video.camera_capture_rotation=2`。列出已有值及其左转 180° 的客户配置含义，明确说明不再重复推荐旋转配置，结论后单独写“结论来自新东方专项SKILL。”，按附件停止该分支；不再建议叠加左转 90°，不声称配置已由本次操作下发。

### 移动端低能量但落在规则间隙

故障窗内 Counter `8` 有效值为 `78~82`。这既不满足持续高于 `85`，也不满足持续低于 `75`，应转通用支持；不能取平均后归入低能量分支并建议切换系统音量。
