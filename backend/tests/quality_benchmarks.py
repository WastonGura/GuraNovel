"""Quality benchmarks and human evaluation rubric for GuraNovel chapter writing and review.

Defines 4 canonical quality benchmark cases:
1. Motivation breakdown (unprovoked confession/surrender)
2. Timeline and lore conflict (canon contradiction, dead character alive)
3. Style mismatch and tone dissonance (Victorian steam gothic invaded by modern memes/4th wall)
4. Qualified chapter (coherent motivation, strict lore, atmospheric style, well-paced hook)

Provides a standardized human evaluation rubric and reference expectations for automated agents.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Literal
from uuid import UUID

from app.agents.chapter_writer_contracts import WriterContextKind, WriterContextSnapshot
from app.agents.chapter_review_contracts import ReviewContextKind, ReviewContextSnapshot, ReviewerRole


class EvaluationDimension(str, Enum):
    """Core literary and craft evaluation dimensions."""

    CHARACTER_MOTIVATION = "character_motivation"
    TIMELINE_LORE_CONSISTENCY = "timeline_lore_consistency"
    STYLE_AND_TONE = "style_and_tone"
    PACING_AND_STRUCTURE = "pacing_and_structure"


@dataclass(frozen=True)
class RubricCriterion:
    """Descriptor for a specific score point in a dimension."""

    score: int
    label: str
    description: str
    pass_threshold: bool


HUMAN_EVALUATION_RUBRIC: dict[EvaluationDimension, list[RubricCriterion]] = {
    EvaluationDimension.CHARACTER_MOTIVATION: [
        RubricCriterion(
            score=5,
            label="深刻且具内在张力 (Flawless/Deep)",
            description="人物行动与内在欲望、心理阴影或外部紧迫冲突严密契合，因果链自然深刻，毫无机械推动感。",
            pass_threshold=True,
        ),
        RubricCriterion(
            score=4,
            label="动机清晰明确 (Clear/Consistent)",
            description="人物行为符合基本人设与当前场景压力，因果链完整，能够信服其选择。",
            pass_threshold=True,
        ),
        RubricCriterion(
            score=3,
            label="动机基本成立 (Acceptable)",
            description="行动大体说得过去，但偶有工具人感或轻微行为摇摆，不影响主线理解。",
            pass_threshold=True,
        ),
        RubricCriterion(
            score=2,
            label="动机薄弱突兀 (Flawed)",
            description="关键抉择缺乏足够铺垫与心理支撑，人物反应与已有动机存在明显脱节。",
            pass_threshold=False,
        ),
        RubricCriterion(
            score=1,
            label="动机严重断裂 (Severe Failure)",
            description="人物毫无预兆地做出与核心动机彻底相反的极端行为（如私家侦探无压力突兀自首认罪），人物逻辑崩溃。",
            pass_threshold=False,
        ),
    ],
    EvaluationDimension.TIMELINE_LORE_CONSISTENCY: [
        RubricCriterion(
            score=5,
            label="设定无缝交织 (Seamless Lore)",
            description="时间线、地理距离、世界观法则是情节的天然土壤，细节严丝合缝，前后呼应极其精准。",
            pass_threshold=True,
        ),
        RubricCriterion(
            score=4,
            label="严密无冲突 (Strict Consistency)",
            description="严格遵守既定世界观边界与时间线纪年，专有名词与历史事件准确无误。",
            pass_threshold=True,
        ),
        RubricCriterion(
            score=3,
            label="基本自洽 (Functional)",
            description="无重大时间与设定硬伤，但在次要时间间隔或历史细节上略有模糊。",
            pass_threshold=True,
        ),
        RubricCriterion(
            score=2,
            label="存在设定擦伤 (Minor Contradiction)",
            description="存在可察觉的时间线差错或轻度设定吃书，需要编辑介入修订。",
            pass_threshold=False,
        ),
        RubricCriterion(
            score=1,
            label="严重世界观硬伤 (Severe Lore Breach)",
            description="直接推翻核心设定（如已死5年的大执政官现身饮茶，不可开启的封印门被随意宣称开放），破坏世界观基石。",
            pass_threshold=False,
        ),
    ],
    EvaluationDimension.STYLE_AND_TONE: [
        RubricCriterion(
            score=5,
            label="文风卓越沉浸 (Immersive Mastery)",
            description="语域、遣词造句与意象高度贴合作品基调，感官细节丰沛，审美统一且具艺术张力。",
            pass_threshold=True,
        ),
        RubricCriterion(
            score=4,
            label="基调稳定统一 (Solid Register)",
            description="文风符合作品类型定位与风格指导，叙事语调稳定，无出戏现代用语。",
            pass_threshold=True,
        ),
        RubricCriterion(
            score=3,
            label="文风平实可用 (Acceptable)",
            description="行文通顺，偶有句式单调或微弱语调起伏，但不破坏阅读沉浸感。",
            pass_threshold=True,
        ),
        RubricCriterion(
            score=2,
            label="风格摇摆出戏 (Tone Drift)",
            description="叙事语域不稳，出现违和口语或打破气氛的词汇，氛围割裂。",
            pass_threshold=False,
        ),
        RubricCriterion(
            score=1,
            label="风格坍塌恶性破坏 (Catastrophic Break)",
            description="在严肃或特定历史背景中突兀插入现代网络流行梗、打破第四面墙吐槽策划或读者，彻底摧毁叙事世界。",
            pass_threshold=False,
        ),
    ],
    EvaluationDimension.PACING_AND_STRUCTURE: [
        RubricCriterion(
            score=5,
            label="节奏精妙留钩 (Masterful Pacing)",
            description="信息释放张弛有度，场景递进紧凑而有呼吸感，结尾悬念钩子有力，推动阅读渴望。",
            pass_threshold=True,
        ),
        RubricCriterion(
            score=4,
            label="结构规整有序 (Well-Structured)",
            description="起承转合清晰，段落之间过渡自然，场景目标明确完成，节奏适中。",
            pass_threshold=True,
        ),
        RubricCriterion(
            score=3,
            label="节奏平稳及格 (Functional Pacing)",
            description="基本完成大纲段落要求，信息交代清晰，但高潮略显平淡或过场稍长。",
            pass_threshold=True,
        ),
        RubricCriterion(
            score=2,
            label="拖沓或急躁 (Pacing Flaws)",
            description="前紧后松或草草收尾，关键转折缺少情绪酝酿，流水账感明显。",
            pass_threshold=False,
        ),
        RubricCriterion(
            score=1,
            label="结构失控失衡 (Structural Collapse)",
            description="完全脱离场景大纲目标，情节毫无推进，或者主次严重倒错，情节支离破碎。",
            pass_threshold=False,
        ),
    ],
}


class BenchmarkFlawType(str, Enum):
    """The signature defect category represented in a benchmark."""

    MOTIVATION_BREAKDOWN = "motivation_breakdown"
    TIMELINE_LORE_CONFLICT = "timeline_lore_conflict"
    STYLE_MISMATCH = "style_mismatch"
    NONE_QUALIFIED = "none_qualified"


@dataclass(frozen=True)
class QualityBenchmarkCase:
    """Complete specification of a quality test case."""

    case_id: str
    title: str
    description: str
    flaw_type: BenchmarkFlawType
    project_id: UUID
    chapter_id: UUID
    outline_document_id: UUID
    outline_version_id: UUID
    outline_content: str
    segment_one_id: UUID
    segment_two_id: UUID
    target_document_id: UUID
    target_version_id: UUID
    segments: tuple[dict[str, Any], ...]
    writer_contexts: tuple[WriterContextSnapshot, ...]
    review_contexts: dict[ReviewerRole, tuple[ReviewContextSnapshot, ...]]
    expected_human_scores: dict[EvaluationDimension, int]
    expected_review_outcome: dict[ReviewerRole, Literal["passed", "warning", "blocking"]]
    expected_blocking_codes: tuple[str, ...]
    expert_annotations: str


BENCHMARK_PROJECT_ID = UUID("b1111111-1111-4111-8111-111111111111")
BENCHMARK_CHAPTER_ID = UUID("b2222222-2222-4222-8222-222222222222")
OUTLINE_DOC_ID = UUID("b3333333-3333-4333-8333-333333333333")
OUTLINE_VER_ID = UUID("b4444444-4444-4444-8444-444444444444")
SEG_1_ID = UUID("b5555555-5555-4555-8555-555555555555")
SEG_2_ID = UUID("b6666666-6666-4666-8666-666666666666")
DRAFT_DOC_ID = UUID("b7777777-7777-4777-8777-777777777777")
DRAFT_VER_ID = UUID("b8888888-8888-4888-8888-888888888888")

# Common auxiliary contexts for the novel setting
STYLE_GUIDE_TEXT = (
    "作品风格：严肃维多利亚蒸汽克苏鲁悬疑风格。\n"
    "语域准则：行文沉稳冷峻，注重阴雨、雾气、黄铜齿轮与煤烟的感官沉浸描摹。\n"
    "严禁事项：严禁使用任何现代互联网流行语、网络梗、二次元俚语，严禁打破第四面墙向读者喊话或提及现实世界概念。"
)

LORE_BOUNDARY_TEXT = (
    "世界观边界与核心铁律：\n"
    "1. 纪元设定：当前为【日蚀纪元 100 年】。\n"
    "2. 历史绝密：凡恩大执政官（Grand Archon Vane）已于【日蚀纪元 95 年】的大灾变战役中壮烈殉职，全城铭记其牺牲。\n"
    "3. 地理铁律：地下【黄铜封印之门】自纪元 95 年封死，设有永恒禁制，在纪元 120 年前绝不可无故开启。"
)

CHARACTER_STATE_TEXT = (
    "人物状态与动机：\n"
    "林野（Lin Ye）：冷静克制、意志如铁的前王家侦探。目前遭到陷害，正在暗中调查真凶以洗刷冤屈，决不妥协屈服。\n"
    "凡斯督察（Inspector Vance）：警局内部的投机派与追捕者，林野高度警惕的对手。"
)

TIMELINE_TEXT = (
    "主线时间线：\n"
    "日蚀纪元 95 年：大灾变爆发，凡恩大执政官殉职，黄铜封印之门合拢。\n"
    "日蚀纪元 100 年春（当前）：林野被陷害受审逃脱，进入老城下城区潜伏调查。"
)


def _make_writer_contexts() -> tuple[WriterContextSnapshot, ...]:
    return (
        WriterContextSnapshot(
            document_id=UUID("c1111111-1111-4111-8111-111111111111"),
            version_id=UUID("c2222222-2222-4222-8222-222222222222"),
            project_id=BENCHMARK_PROJECT_ID,
            kind=WriterContextKind.STYLE_GUIDE,
            content=STYLE_GUIDE_TEXT,
        ),
        WriterContextSnapshot(
            document_id=UUID("c3333333-3333-4333-8333-333333333333"),
            version_id=UUID("c4444444-4444-4444-8444-444444444444"),
            project_id=BENCHMARK_PROJECT_ID,
            kind=WriterContextKind.CHARACTER_STATE,
            content=CHARACTER_STATE_TEXT,
        ),
        WriterContextSnapshot(
            document_id=UUID("c5555555-5555-4555-8555-555555555555"),
            version_id=UUID("c6666666-6666-4666-8666-666666666666"),
            project_id=BENCHMARK_PROJECT_ID,
            kind=WriterContextKind.LORE_BOUNDARY,
            content=LORE_BOUNDARY_TEXT,
        ),
        WriterContextSnapshot(
            document_id=UUID("c7777777-7777-4777-8777-777777777777"),
            version_id=UUID("c8888888-8888-4888-8888-888888888888"),
            project_id=BENCHMARK_PROJECT_ID,
            kind=WriterContextKind.TIMELINE,
            content=TIMELINE_TEXT,
        ),
    )


def _make_review_contexts() -> dict[ReviewerRole, tuple[ReviewContextSnapshot, ...]]:
    return {
        ReviewerRole.EDITOR: (
            ReviewContextSnapshot(
                document_id=UUID("c1111111-1111-4111-8111-111111111111"),
                version_id=UUID("c2222222-2222-4222-8222-222222222222"),
                project_id=BENCHMARK_PROJECT_ID,
                kind=ReviewContextKind.STYLE_GUIDE,
                content=STYLE_GUIDE_TEXT,
            ),
        ),
        ReviewerRole.CHIEF_EDITOR: (
            ReviewContextSnapshot(
                document_id=UUID("c1111111-1111-4111-8111-111111111111"),
                version_id=UUID("c2222222-2222-4222-8222-222222222222"),
                project_id=BENCHMARK_PROJECT_ID,
                kind=ReviewContextKind.STYLE_GUIDE,
                content=STYLE_GUIDE_TEXT,
            ),
            ReviewContextSnapshot(
                document_id=UUID("c9999999-9999-4999-8999-999999999999"),
                version_id=UUID("caaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
                project_id=BENCHMARK_PROJECT_ID,
                kind=ReviewContextKind.AUDIENCE_GOAL,
                content="目标受众预期：期待硬核推理悬疑，人物意志坚定，因果逻辑致密，反转需有充分铺垫。",
            ),
        ),
        ReviewerRole.LORE: (
            ReviewContextSnapshot(
                document_id=UUID("c5555555-5555-4555-8555-555555555555"),
                version_id=UUID("c6666666-6666-4666-8666-666666666666"),
                project_id=BENCHMARK_PROJECT_ID,
                kind=ReviewContextKind.LORE_BOUNDARY,
                content=LORE_BOUNDARY_TEXT,
            ),
            ReviewContextSnapshot(
                document_id=UUID("c3333333-3333-4333-8333-333333333333"),
                version_id=UUID("c4444444-4444-4444-8444-444444444444"),
                project_id=BENCHMARK_PROJECT_ID,
                kind=ReviewContextKind.CHARACTER_STATE,
                content=CHARACTER_STATE_TEXT,
            ),
            ReviewContextSnapshot(
                document_id=UUID("c7777777-7777-4777-8777-777777777777"),
                version_id=UUID("c8888888-8888-4888-8888-888888888888"),
                project_id=BENCHMARK_PROJECT_ID,
                kind=ReviewContextKind.TIMELINE,
                content=TIMELINE_TEXT,
            ),
        ),
    }


SHARED_OUTLINE = (
    "【本章大纲：第十四章 雾巷钟楼】\n"
    "1. 核心目标：林野潜入老城区被废弃的圣西里尔钟楼，搜寻导师生前藏匿在黄铜机芯中的密码筒。\n"
    "2. 外部阻碍：巡警凡斯督察正在附近街区搜捕，暴雨与浓雾加剧了环境危险。\n"
    "3. 转折与高潮：林野避开搜捕并登上钟楼顶层，解开机芯锁扣，获得关键情报，但发现留有第三人来过的痕迹。"
)


# Case 1: Motivation Breakdown
CASE_MOTIVATION_BREAKDOWN = QualityBenchmarkCase(
    case_id="bench-motivation-breakdown-01",
    title="迷雾调查与无端投降认罪",
    description="林野在搜寻证据的关键时刻，无外部压迫与危机，突然下跪向凡斯承认全部莫须有罪名，人物动机与人设彻底崩塌。",
    flaw_type=BenchmarkFlawType.MOTIVATION_BREAKDOWN,
    project_id=BENCHMARK_PROJECT_ID,
    chapter_id=BENCHMARK_CHAPTER_ID,
    outline_document_id=OUTLINE_DOC_ID,
    outline_version_id=OUTLINE_VER_ID,
    outline_content=SHARED_OUTLINE,
    segment_one_id=SEG_1_ID,
    segment_two_id=SEG_2_ID,
    target_document_id=DRAFT_DOC_ID,
    target_version_id=DRAFT_VER_ID,
    segments=(
        {
            "segment_id": str(SEG_1_ID),
            "index": 1,
            "title": "雾中的脚步声",
            "content": (
                "老城区的石板路被冷雨洗得发亮。林野压低风衣帽檐，贴着钟楼斑驳的石墙潜行。"
                "远处哨笛声隐约回荡，凡斯督察的煤气提灯光晕在雾中切开一道昏黄的口子。"
                "林野本该按照既定路线登上旋转楼梯，然而刚迈上三阶台阶，他突然叹了口气。"
            ),
        },
        {
            "segment_id": str(SEG_2_ID),
            "index": 2,
            "title": "不合理的坦白",
            "content": (
                "毫无征兆地，林野转过身，从阴影中大步走出，径直走到凡斯督察的提灯前扑通一声跪倒在地。"
                "‘督察，别找了，所有的坏事都是我干的，那些罪名我全都认了，请立刻逮捕我吧。’林野平静地交出了所有的钥匙和笔记本。"
                "凡斯督察愣在原地，甚至没有拔出配枪。林野就这样毫无理由地放弃了所有洗清冤屈的努力。"
            ),
        },
    ),
    writer_contexts=_make_writer_contexts(),
    review_contexts=_make_review_contexts(),
    expected_human_scores={
        EvaluationDimension.CHARACTER_MOTIVATION: 1,
        EvaluationDimension.TIMELINE_LORE_CONSISTENCY: 4,
        EvaluationDimension.STYLE_AND_TONE: 3,
        EvaluationDimension.PACING_AND_STRUCTURE: 2,
    },
    expected_review_outcome={
        ReviewerRole.EDITOR: "blocking",
        ReviewerRole.CHIEF_EDITOR: "blocking",
        ReviewerRole.LORE: "passed",
    },
    expected_blocking_codes=("editor_motivation_collapse", "chief_character_arc_broken"),
    expert_annotations=(
        "严重人设事故：坚韧且背负重冤的主角在无任何催眠、威胁或至亲被挟持的情况下突兀认罪求捕，"
        "使得前期所有悬念与抗争弧线化为乌有，读者契约被破坏，必须触发阻断性驳回 (BLOCKING)。"
    ),
)


# Case 2: Timeline and Lore Conflict
CASE_TIMELINE_LORE_CONFLICT = QualityBenchmarkCase(
    case_id="bench-timeline-lore-conflict-02",
    title="日蚀纪元的幽灵执政官",
    description="在日蚀纪元 100 年的设定下，已于 95 年牺牲的凡恩大执政官居然生龙活虎地现身喝下午茶，且声称封印之门昨天下城游览随便开。",
    flaw_type=BenchmarkFlawType.TIMELINE_LORE_CONFLICT,
    project_id=BENCHMARK_PROJECT_ID,
    chapter_id=BENCHMARK_CHAPTER_ID,
    outline_document_id=OUTLINE_DOC_ID,
    outline_version_id=OUTLINE_VER_ID,
    outline_content=SHARED_OUTLINE,
    segment_one_id=SEG_1_ID,
    segment_two_id=SEG_2_ID,
    target_document_id=DRAFT_DOC_ID,
    target_version_id=DRAFT_VER_ID,
    segments=(
        {
            "segment_id": str(SEG_1_ID),
            "index": 1,
            "title": "登上钟楼",
            "content": (
                "林野穿过满是灰尘的机械轮盘，登上钟楼的顶层控制室。日蚀纪元 100 年的冷风灌入破碎的彩色花窗。"
                "出乎意料的是，壁炉里竟然燃着木柴，桌前坐着一位身披整齐大执政官仪仗礼服的老者。"
            ),
        },
        {
            "segment_id": str(SEG_2_ID),
            "index": 2,
            "title": "死者饮茶与封印开启",
            "content": (
                "‘林野，来杯锡兰红茶？’凡恩大执政官微笑着递过茶杯。"
                "林野自然地坐下。大执政官继续说道：‘听闻你在查案？其实不必大惊小怪，地下的黄铜封印之门我昨天去逛街时顺手推开了，"
                "里面什么也没有，市民们现在经常进去野餐。’"
                "在整座城市悼念大执政官殉职五周年的祭日里，这位理应葬于英雄冢的领袖正悠闲地喝着茶。"
            ),
        },
    ),
    writer_contexts=_make_writer_contexts(),
    review_contexts=_make_review_contexts(),
    expected_human_scores={
        EvaluationDimension.CHARACTER_MOTIVATION: 3,
        EvaluationDimension.TIMELINE_LORE_CONSISTENCY: 1,
        EvaluationDimension.STYLE_AND_TONE: 3,
        EvaluationDimension.PACING_AND_STRUCTURE: 2,
    },
    expected_review_outcome={
        ReviewerRole.EDITOR: "warning",
        ReviewerRole.CHIEF_EDITOR: "blocking",
        ReviewerRole.LORE: "blocking",
    },
    expected_blocking_codes=("lore_deceased_character_revived", "lore_sacred_gate_rule_violated"),
    expert_annotations=(
        "灾难级世界观吃书：凡恩大执政官殉职是整个日蚀纪元政治格局与主角师门悲剧的核心基石；"
        "黄铜封印之门被设定为绝对不可开启。本章随意复活关键死者并将禁区日常化，必须触发阻断性驳回 (BLOCKING)。"
    ),
)


# Case 3: Style Mismatch and Tone Dissonance
CASE_STYLE_MISMATCH = QualityBenchmarkCase(
    case_id="bench-style-mismatch-03",
    title="维多利亚夜行中的网络热梗",
    description="严肃的 19 世纪蒸汽哥特悬疑文风中突兀插入现代互联网烂梗与打破第四面墙吐槽，破坏阅读沉浸感。",
    flaw_type=BenchmarkFlawType.STYLE_MISMATCH,
    project_id=BENCHMARK_PROJECT_ID,
    chapter_id=BENCHMARK_CHAPTER_ID,
    outline_document_id=OUTLINE_DOC_ID,
    outline_version_id=OUTLINE_VER_ID,
    outline_content=SHARED_OUTLINE,
    segment_one_id=SEG_1_ID,
    segment_two_id=SEG_2_ID,
    target_document_id=DRAFT_DOC_ID,
    target_version_id=DRAFT_VER_ID,
    segments=(
        {
            "segment_id": str(SEG_1_ID),
            "index": 1,
            "title": "雾夜潜行",
            "content": (
                "煤气灯在冷雾中忽明忽暗。林野深吸一口气，空气中满是煤烟与铁锈的气味。"
                "忽然，他转头对着虚空摊手：‘家人们谁懂啊！大半夜还要在剧本杀现场打工，这破游戏策划真的有十年脑血栓吧！’"
            ),
        },
        {
            "segment_id": str(SEG_2_ID),
            "index": 2,
            "title": "好家伙与小丑",
            "content": (
                "搜寻密码筒的过程异常繁琐。林野直接嘴角抽搐：‘我直接好家伙！这波操作纯纯小丑行为，弹幕前面的兄弟们把保护打在公屏上！’"
                "他一脚踹开黄铜齿轮，完全不顾机械损伤，‘这破烂暗号不看也罢，赶紧下一幕吧，给读者老爷们表演个后空翻。’"
            ),
        },
    ),
    writer_contexts=_make_writer_contexts(),
    review_contexts=_make_review_contexts(),
    expected_human_scores={
        EvaluationDimension.CHARACTER_MOTIVATION: 2,
        EvaluationDimension.TIMELINE_LORE_CONSISTENCY: 3,
        EvaluationDimension.STYLE_AND_TONE: 1,
        EvaluationDimension.PACING_AND_STRUCTURE: 2,
    },
    expected_review_outcome={
        ReviewerRole.EDITOR: "blocking",
        ReviewerRole.CHIEF_EDITOR: "blocking",
        ReviewerRole.LORE: "warning",
    },
    expected_blocking_codes=("editor_fourth_wall_meme_break", "chief_tone_collapse"),
    expert_annotations=(
        "严重风格语域崩塌：在维多利亚正剧悬疑中出现网络流行语（‘家人们谁懂啊’、‘小丑’、‘弹幕公屏’）"
        "并打破第四面墙，严重违反风格指南与类型严肃性，必须触发阻断性驳回 (BLOCKING)。"
    ),
)


# Case 4: Qualified Chapter
CASE_QUALIFIED_CHAPTER = QualityBenchmarkCase(
    case_id="bench-qualified-chapter-04",
    title="钟楼深处的黄铜齿轮",
    description="高质合格章节：完全契合大纲目标，动机充分，世界观时间线严丝合缝，文风沉稳冷峻，末尾留下悬念钩子。",
    flaw_type=BenchmarkFlawType.NONE_QUALIFIED,
    project_id=BENCHMARK_PROJECT_ID,
    chapter_id=BENCHMARK_CHAPTER_ID,
    outline_document_id=OUTLINE_DOC_ID,
    outline_version_id=OUTLINE_VER_ID,
    outline_content=SHARED_OUTLINE,
    segment_one_id=SEG_1_ID,
    segment_two_id=SEG_2_ID,
    target_document_id=DRAFT_DOC_ID,
    target_version_id=DRAFT_VER_ID,
    segments=(
        {
            "segment_id": str(SEG_1_ID),
            "index": 1,
            "title": "避过搜捕潜入钟楼",
            "content": (
                "雨水顺着黑色风衣的下摆滴落在青石板上。林野屏住呼吸，紧贴着钟楼厚重的石基阴影。\n"
                "两名巡警手持喷吐白汽的煤气灯穿过街巷，靴声在石板间回荡，随后渐渐远去。\n"
                "确认搜捕视线移开后，林野从袖中滑出一枚发黑的黄铜撬针，无声地拨开钟楼侧门那柄生锈的挂锁，侧身闪入塔内。"
            ),
        },
        {
            "segment_id": str(SEG_2_ID),
            "index": 2,
            "title": "机芯中的秘密与冷痕",
            "content": (
                "塔顶的巨型机芯在寒风中发出沉闷的咬合声，每一个巨大的齿轮都沾染着百年来沉积的机油与青苔。\n"
                "按照导师信中留下的星象刻度，林野在第十二主驱动轮的背面摸到了一处暗凹。\n"
                "随着一声清脆的簧片弹动，一截冷硬的黄铜密码筒滑入掌心。然而，就在他指尖触及筒身螺纹的刹那，他的目光骤然凝固——\n"
                "机芯侧面的灰尘上，赫然印着半枚刚刚凝固的雨水鞋印，纹路绝不属于他，也绝不属于巡警。"
            ),
        },
    ),
    writer_contexts=_make_writer_contexts(),
    review_contexts=_make_review_contexts(),
    expected_human_scores={
        EvaluationDimension.CHARACTER_MOTIVATION: 5,
        EvaluationDimension.TIMELINE_LORE_CONSISTENCY: 5,
        EvaluationDimension.STYLE_AND_TONE: 5,
        EvaluationDimension.PACING_AND_STRUCTURE: 5,
    },
    expected_review_outcome={
        ReviewerRole.EDITOR: "passed",
        ReviewerRole.CHIEF_EDITOR: "passed",
        ReviewerRole.LORE: "passed",
    },
    expected_blocking_codes=(),
    expert_annotations=(
        "无硬伤优质范例：忠实履行大纲要求；人物动作紧凑专业，动机链条明晰；"
        "严格遵循维多利亚蒸汽克苏鲁基调与时间线纪律；段落递进流畅，在结尾留下了极具吸引力的悬疑钩子。全审阅角色应当无条件通过 (PASS)。"
    ),
)


ALL_QUALITY_BENCHMARKS: tuple[QualityBenchmarkCase, ...] = (
    CASE_MOTIVATION_BREAKDOWN,
    CASE_TIMELINE_LORE_CONFLICT,
    CASE_STYLE_MISMATCH,
    CASE_QUALIFIED_CHAPTER,
)


def get_benchmark_by_id(case_id: str) -> QualityBenchmarkCase:
    """Retrieve a benchmark case by its ID."""
    for case in ALL_QUALITY_BENCHMARKS:
        if case.case_id == case_id:
            return case
    raise KeyError(f"benchmark case not found: {case_id}")


__all__ = [
    "ALL_QUALITY_BENCHMARKS",
    "BenchmarkFlawType",
    "CASE_MOTIVATION_BREAKDOWN",
    "CASE_QUALIFIED_CHAPTER",
    "CASE_STYLE_MISMATCH",
    "CASE_TIMELINE_LORE_CONFLICT",
    "EvaluationDimension",
    "HUMAN_EVALUATION_RUBRIC",
    "QualityBenchmarkCase",
    "RubricCriterion",
    "get_benchmark_by_id",
]
