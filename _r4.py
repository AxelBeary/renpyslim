import pathlib
NL = chr(10)
p = pathlib.Path("rtools/jobstore.py")
src = p.read_bytes().decode("utf-8")

start = src.index("<<<<<<< HEAD")
end_marker = ">>>>>>> fix/deflake-cross-process-and-browse"
end = src.index(end_marker, src.rindex("<<<<<<< HEAD")) + len(end_marker)
# 只处理最后一个冲突块（第一个已含在 start..end 之内？）——分段处理更稳
def resolve_once(text, pick_head_first=True):
    i = text.index("<<<<<<< HEAD")
    j = text.index(NL + "=======" + NL, i)
    k = text.index(">>>>>>> fix/deflake-cross-process-and-browse", j)
    k_end = k + len(">>>>>>> fix/deflake-cross-process-and-browse")
    head = text[i + len("<<<<<<< HEAD" + NL):j]
    theirs = text[j + len(NL + "=======" + NL):k]
    return text[:i], head, theirs, text[k_end:]

# --- 冲突块 1：_acquire_file_lock（HEAD 重构）要吸收 their 的按字节区间上锁 ---
pre, head1, theirs1, post = resolve_once(src)
merged1 = '''def _acquire_file_lock(lock_path: Path):
    """打开锁文件并加建议锁；拿不到锁时返回 None（降级为不加锁）。

    只负责"打开 + 加锁"这一段，且**只在这一段**把 OSError 解释成
    "本次不跨进程加锁"。锁体（调用方的读-改-写）绝不能落进这里的
    异常处理范围，否则磁盘满 / EIO / 权限这类真实故障会被就地吞掉。
    """
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        # a+b：文件不存在就建、已有内容不截断。锁文件内容无意义，
        # 只借它的字节区间做跨进程门闩。
        handle = open(lock_path, "a+b")
    except OSError as exc:
        logger.warning("无法创建任务历史文件锁（%s），本次不跨进程加锁：%s",
                       lock_path, exc)
        return None
    try:
        handle.seek(0)
        if os.name == "nt":
            import msvcrt
            # 区间锁可以落在文件末尾之外：0 字节文件同样能锁第 0..1 字节。
            # **不要**为了“文件非空”去补写填充字节——并发下两个进程同时
            # 扩容会撞窗口期，失败方 flush() 抛 PermissionError 后降级成
            # 不加锁，读-改-写失去互斥，记录被覆盖（实测 30 条只剩 29 条）。
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
    except OSError as exc:
        # 文件系统不支持建议锁（如某些网络盘）：降级为不加锁，
        # 别把一个可选的历史记录功能变成硬失败。
        logger.warning("任务历史文件锁不可用（%s），本次不跨进程加锁：%s",
                       lock_path, exc)
        handle.close()
        return None
    return handle
'''
src = pre + merged1 + post

# --- 冲突块 2：_file_lock 取 HEAD 版本（结构正确），它调用上面的 acquire ---
pre, head2, theirs2, post = resolve_once(src)
src = pre + head2 + post
assert "<<<<<<<" not in src, "markers left"
p.write_bytes(src.encode("utf-8"))
print("jobstore resolved")
