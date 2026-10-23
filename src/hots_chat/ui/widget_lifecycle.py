"""在UI线程结束自有控件生命周期；不修改仍然存活的父子关系。"""
import tkinter as tk


def widget_descendants(widget):
    result=[]
    pending=list(widget.children.values())
    while pending:
        child=pending.pop()
        result.append(child)
        pending.extend(child.children.values())
    return result


def detach_destroyed_tree(widget,descendants):
    """仅在Tk递归destroy返回后调用，解除自建子控件缓存的master回边。"""
    for child in descendants:child.master=None
    widget.master=None


def clear_variable_traces(variable):
    """移除本组件拥有的变量回调，避免Tcl命令继续持有Python组件。"""
    if isinstance(variable,tk.Variable):
        for modes,command in variable.trace_info():
            variable.trace_remove(modes,command)
