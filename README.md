# VEXAutoViz

2D 路径可视化工具 — 打开 VEX 自动赛的 `Auto.cpp`，在地图上看到底盘轨迹，点击代码行 ↔ 点击轨迹段双向联动。

**目标用户场景**：写完自动赛代码后，看不懂某行代码对应哪个动作？打开这个工具，点击 `chassis.turn_to_angle(-90,...)` 那一行，画布上对应的转向点会高亮黄色。

## 它做什么

- 解析 `chassis.drive_distance / turn_to_angle / turn_LR_angle / drive_stop` 四种命令
- 把每条命令按 (x, y, heading) 模拟出来，0° = 北 (y+)，点转向模型
- 在 Canvas 上画出：直行线段（电压决定颜色）+ 转向楔形（磁红/青）+ 制动点（红/黄/灰）
- 左栏列表 + 右栏画布；点击任一侧，对应项目在另一侧高亮黄色
- `chassis.get_absolute_heading()` 自动解析为当前航向（包括 `get_absolute_heading() - 180` 这种相对转技巧）
- 设置面板：起点 X/Y/航向、缩放、轮胎直径、轮距；保存到 `~/.vexautoviz.json`
- 路径端点标记：右键 listbox 行或画布上的 marker 即可把段终点标成青蓝色菱形 (`🔖 L<n>`)；标记只在本次会话内有效，重启重置
- 删除段（区别于隐藏）：弹 modal 确认后直接从 `Auto.cpp` 删除该行，文件会被改写；标记随段自动清理；可通过 git 恢复
- 画布绘制 polyline：工具栏 `[📐 绘制]` 进入绘制模式 → 右键选起点（必须是某段 END）→ 左键连续加点（近标记自动吸附）→ ESC 完成；自动生成 `turn_to_angle` + `drive_distance` 行并插入 `Auto.cpp`

## 不做什么 (MVP 故意不做)

- VEX 原生 `Drivetrain` / `smartdrive` / `Motor.spinFor`
- 弧线转向（设置里留了 `track_width` 钩子，未来加）
- 时间/速度仿真（轨迹是纯几何）
- 陀螺漂移建模

## 安装 / 运行

依赖已经在系统预装 (miniconda3 Python 3.12+):

```
tkinter, ttkbootstrap, Pillow
```

启动：

```bash
cd C:/Users/liuzhen/Desktop/coding/projects/VEXAutoViz
"C:/Users/liuzhen/miniconda3/python.exe" main.py
```

打开文件对话框选 `Auto.cpp`（或在工具栏点「打开 Auto.cpp」）。

> MSYS2 自带 Python 没有 `ttkbootstrap`，必须用 miniconda3 的 Python。

## 测试

```bash
"C:/Users/liuzhen/miniconda3/python.exe" -m pytest tests/ -v
```

23 个单元测试覆盖：4 种命令形态、默认参数补齐、嵌套 `get_absolute_heading()` 表达式、块/行注释剥离、行号追踪、`chassis.get_absolute_heading() - 180` 相对转解析、对真实 `蓝远/Auto.cpp` 的端到端解析，以及 `drawing.py` 的 heading 计算 / C++ 行格式化 / polyline→多段命令生成（直角转弯、短距离忽略、heading 阈值、渲染往返）。

> `tests/test_parser.py` 里有 2 个 pre-existing 失败（`Simulator()` 不再吃无参、`蓝远/Auto.cpp` 行数漂移）— 都是测试代码过期，runtime (`app/ui.py`) 不受影响。

## 项目结构

```
VEXAutoViz/
├── main.py                 # 启动 MainWindow
├── app/
│   ├── commands.py         # ChassisCommand / TrajectorySegment / CommandKind
│   ├── parser.py           # CppParser — regex-based, comment-aware
│   ├── simulator.py        # Simulator — point-turn model
│   ├── highlight.py        # HighlightMap — line ↔ segment 双向查找
│   ├── settings.py         # Settings dataclass + ~/.vexautoviz.json 持久化
│   ├── drawing.py          # Polyline → C++ command generation (heading / drive / turn)
│   └── ui.py               # MainWindow + SettingsDialog
├── tests/
│   ├── test_parser.py
│   └── test_drawing.py
├── requirements.txt
└── README.md
```

## 坐标系约定

- 场坐标系 = 英制 (inches)，0° = 北 (y+)
- 起点 (initial_x, initial_y, initial_heading) 由设置面板控制，默认 (0, 0, 0)
- 画布中心 = 起点 (px/in 由设置 `pixels_per_inch` 控制，0 = 自动适配)
- y 轴在画布上反转 (canvas y 朝下)

## 已知限制 / 后续工作

1. 弧线转向 — `track_width` 已经在 settings 暴露，但 simulator 仍按点转向处理。要加弧线：在 `Simulator._turn` 写新的 `_turn_arc` 方法，根据 `track_width` 沿弧线更新 `(x, y)`。
2. VEX 原生 API — `commands.CommandKind` 是开放枚举，加 `DRIVETRAIN` / `MOTHER_SPIN_FOR` 一条新枚举值 + parser 一条新正则 + simulator 一个新方法即可，不需重构。
3. 列表上 hover 显示原始代码行 — tkinter Listbox 原生不支持 tooltip，要做的话用 `Toplevel` 模拟。
4. 多文件对比视图 — 4 个兄弟项目能在同一画布叠加吗？目前一次只显示一个。

## 画布手势

| 手势 | 行为 | 影响的状态 |
|------|------|-----------|
| **罗盘拖拽** (右上角 HUD) | 围绕中心旋转世界视图 | 只改 `view_rotation`，持久化 |
| **画布拖拽** (单指 / 鼠标左键，>5px) | 平移画布 | 只改 `view_offset_x/y`，**不** 持久化 |
| **鼠标滚轮** (双指触摸板手势) | 以光标为锚点缩放 | 只改 `_scale`，范围 `[0.5, 50]` |
| **快速点击** (<5px) | 触发列表 ↔ 画布 双向高亮 | — |
| **"复位视角" 按钮** | 全部还原 | rotation=0、offset=0、scale=自动适配 |

三种手势**互不干扰**：罗盘绝不调平移/缩放；画布拖拽绝不调旋转/缩放；滚轮绝不调旋转/平移。手动缩放后再点"复位视角"或"重新加载"，缩放回到自动适配状态。

## 编辑快捷键

| 按键 / 操作 | 行为 |
|-------------|------|
| `D` 或工具栏 `[📐 绘制]` | 进入 / 退出绘制模式 |
| 绘制模式 + 右键某段 END | 选起点 |
| 绘制模式 + 左键 | 加 polyline 点（距标记点 ≤3in 自动吸附） |
| `Esc`（绘制中） | 完成 → 把 polyline 写成 C++ 插到 `Auto.cpp` |
| `Esc`（绘制中无点） | 退出绘制模式 |
| `Delete` / `BackSpace` | 删除当前选中段（弹确认后改 `Auto.cpp`） |
| listbox 行右键 → `标记此段终点 🔖` / `取消标记` | toggle 标记 |
| 画布右键 marker → `取消标记` | 移除单点标记 |