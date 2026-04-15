import sys
sys.path.insert(0, "/Users/laiyuewei/Desktop/ComfyUI.dev/ComfyUI")
try:
    from custom_nodes.ComfyUI_Prompt_Assistant import __init__ as pa
    print("Extension loaded successfully")
except Exception as e:
    import traceback
    traceback.print_exc()
