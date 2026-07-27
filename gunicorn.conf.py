# Gunicorn lee este archivo automáticamente al arrancar (no depende del
# Start Command de Render). Da tiempo suficiente para las operaciones largas
# (búsqueda de fotos y descripciones con IA, que usan búsqueda web).
timeout = 180
graceful_timeout = 180
threads = 4
workers = 2
