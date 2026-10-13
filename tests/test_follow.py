"""增量追加（Follow 的核心算法）—— **纯函数，全离线**。

覆盖 `core/append.py` 与 `ReadTask.append` 的判定规则：
「只追加已定稿的句子；前文被改写就不追加」。

⚠️ 测试用句都**刻意超过 8 个字**：`split_sentences` 有一个"过短的句子并进下一句"
的合并步骤（`MIN_SENTENCE_LEN = 8`），中文短句很容易被并掉 ——
不了解这点会写出"看起来对、其实测的是别的行为"的断言（我第一版就是这样）。
"""

import asyncio

from nekopal.core.append import plan_append, stable_prefix
from nekopal.core.models import ReadRequest, SynthOptions
from nekopal.core.pipeline import SentenceStream, prepare_sentences
from nekopal.server.tasks import ReadTask

#: 三句都 ≥ 8 字，因此各自独立成句
A = "第一句话它足够长了。"
B = "第二句话它也足够长了。"
C = "第三句话同样也够长了。"
#: 还没写完的尾巴 —— 会被并进**上一句**
TAIL = "半句还没写完"


def _split(text):
    """只分句、不清洗（清洗规则与增量判定无关，测试里去掉噪音）。"""

    return prepare_sentences(text, {}, [])


# ---- stable_prefix -------------------------------------------------------

def test_stable_prefix_drops_the_last_sentence():
    assert stable_prefix([A, TAIL]) == [A]
    assert stable_prefix([A]) == []
    assert stable_prefix([]) == []


# ---- plan_append ---------------------------------------------------------

def test_nothing_is_stable_while_there_is_only_one_element():
    # A 后面还挂着没写完的尾巴 → 合成一句，还没有"定稿"的
    assert _split(A + TAIL) == [A + TAIL]
    assert plan_append([], _split(A + TAIL)) == []


def test_first_stable_sentence_can_be_read():
    assert plan_append([], _split(A + B)) == [A]


def test_growing_last_sentence_appends_nothing():
    """最后一句继续变长 —— 它不是"新增"，不能被重复念。"""

    assert plan_append([A], _split(A + B + TAIL)) == []


def test_next_sentence_is_appended():
    assert plan_append([A], _split(A + B + C)) == [B]


def test_final_takes_everything_left():
    assert plan_append([A], _split(A + B + C), final=True) == [B, C]
    # 尾巴没标点也要在 final 时收走（它会被并进上一句）
    assert plan_append([A], _split(A + B + TAIL), final=True) == [B + TAIL]


def test_conflict_when_the_previous_sentence_itself_changed():
    """极端情况：连"已经念过的那句"都被改了 → 只能报冲突，不能重念。"""

    assert plan_append([A], _split(A + TAIL), final=True) is None


def test_rewrite_is_refused():
    """前文被改写 → None（已念出去的收不回来，宁可不补也不重念）。"""

    assert plan_append([A], _split("完全换了一句话，而且足够长。")) is None


def test_shrinking_is_refused():
    assert plan_append([A, B], _split(A)) is None


def test_repeating_the_same_text_is_idempotent():
    assert plan_append([A], _split(A + B)) == []


# ---- ReadTask.append 的行为 ---------------------------------------------

def _streaming_task(text=A + B):
    request = ReadRequest(
        text=text, voice="say:Tingting", source={"kind": "block"}, options={"follow": True}
    )
    return ReadTask("tsk_test", request, _split(text), SynthOptions(), streaming=True)


def test_streaming_task_starts_with_nothing_committed():
    """流式任务一开始**不念任何句子** —— 它们还没定稿（否则最后一句一变大就被判成改写）。"""

    task = _streaming_task()

    assert task.sentence_count == 0
    assert task.submitted == []

    report = task.append(A + B, default_clean=[])
    assert report["appended"] == 1
    assert task.submitted == [A]


def test_streaming_task_appends_then_closes_on_final():
    task = _streaming_task()
    task.append(A + B, default_clean=[])

    assert task.append(A + B + TAIL, default_clean=[])["appended"] == 0
    assert not task.stream.closed

    report = task.append(A + B + C, final=True, default_clean=[])
    assert report["appended"] == 2
    assert task.stream.closed


def test_streaming_task_records_conflicts():
    task = _streaming_task()
    task.append(A + B, default_clean=[])

    report = task.append("整段被换掉了，而且足够长。", default_clean=[])

    assert report["conflict"] is True
    assert task.conflicts == 1


def test_appending_to_a_closed_task_is_a_noop():
    """普通（整篇）朗读的任务是**已关闭**的流 —— 追加应当被安静地忽略。"""

    request = ReadRequest(text="随便", voice="say:Tingting", source={}, options={})
    task = ReadTask("tsk_plain", request, _split(A), SynthOptions())

    report = task.append("完全不同的内容，也足够长了。", default_clean=[])

    assert report["closed"] is True
    assert report["appended"] == 0


# ---- SentenceStream ------------------------------------------------------

def test_sentence_stream_waits_then_returns():
    """等新句子：追加后立刻返回；关闭且不够长则返回 False。"""

    async def scenario():
        stream = SentenceStream()
        timer = asyncio.get_running_loop().call_later(0.05, lambda: stream.append([B]))
        try:
            assert await stream.wait_for(0) is True
            assert stream.has(0) is True
            assert stream.has(1) is False

            stream.close()
            assert await stream.wait_for(9) is False
        finally:
            timer.cancel()

    asyncio.run(scenario())


def test_sentence_stream_batches_announcements():
    stream = SentenceStream([A])
    stream.take_announcements()  # 丢弃初始批次

    start = stream.append([B, C])

    assert start == 1
    assert stream.take_announcements() == [(1, [B, C])]
    assert stream.take_announcements() == []
