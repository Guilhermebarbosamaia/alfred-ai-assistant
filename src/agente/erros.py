class ErroProvedor(RuntimeError):
    """Falha ao conversar com o provedor do modelo (Claude, Ollama...).

    A mensagem é exibida ao usuário: deve ser amigável, em português e nunca
    conter o conteúdo da conversa nem segredos.
    """
