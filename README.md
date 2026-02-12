
基于 Python Flask-SocketIO 构建的高实时性、全功能聊天系统。支持媒体文件传输、私密对话、全局监控、动态内网穿透及自动图片压缩。

latest release: v2.0  重构项目，将项目文件模块化，优化文件结构。

内网穿透的实现是基于ngrok的，NGROK_TOKEN需要注册ngrok账户获取。客户端访问https://api.npoint.io/{JSON_BIN_ID}获取ngrok生成的域名。

ps：ngrok若被添加到系统环境中，则不用在server本地根目录使用ngrok.exe，否则需要自备ngrok.exe。使用自备ngrok时可能需要关闭系统防火墙实时保护。

任何因使用ngrok造成的损失，作者不为此负责。

谁有latest stable的ngrok安装包给我一个呗（）

此项目的问题包含但不限于：结构混乱、AI生成、注释缺失、注释可读性差、冗余代码、无用代码。

License: This project is licensed under CC BY-NC 4.0. You are free to use it for personal or educational purposes, but commercial use is strictly prohibited.
