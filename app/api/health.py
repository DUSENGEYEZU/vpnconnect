from app.api import api_bp


@api_bp.get("/health")
def health():
    """Liveness check.
    ---
    tags:
      - Health
    responses:
      200:
        description: The API is up.
        content:
          application/json:
            schema:
              type: object
              properties:
                status:
                  type: string
                  example: ok
    """
    return {"status": "ok"}
