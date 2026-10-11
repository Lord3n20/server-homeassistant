FROM itzg/minecraft-server:latest

COPY run.sh /run.sh
COPY konsole.py /konsole.py
RUN chmod +x /run.sh
ENTRYPOINT [ "/run.sh" ]
