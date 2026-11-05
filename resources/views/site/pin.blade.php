<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Вход</title>
    <style>
        body { margin: 0; min-height: 100vh; display: flex; align-items: center; justify-content: center; background: #0b0f19; color: #e2e8f0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
        form { width: 280px; background: #131a2a; border: 1px solid #222d45; border-radius: 10px; padding: 24px; }
        h1 { font-size: 18px; margin: 0 0 14px; }
        input { width: 100%; box-sizing: border-box; background: #0b0f19; color: #fff; border: 1px solid #222d45; border-radius: 8px; padding: 12px; font-size: 20px; letter-spacing: 0.3em; text-align: center; }
        button { width: 100%; margin-top: 12px; background: #3b82f6; color: #fff; border: 0; border-radius: 8px; padding: 12px; font-size: 15px; font-weight: 600; cursor: pointer; }
        p { color: #ef4444; font-size: 13px; margin: 10px 0 0; }
    </style>
</head>
<body>
    <form method="post" action="/pin" autocomplete="off">
        <h1>Пинкод счёта</h1>
        <input name="pin" type="password" inputmode="numeric" autocomplete="off" maxlength="12" autofocus required>
        <button type="submit">Открыть</button>
        @if($bad)
            <p>Неверный пинкод</p>
        @endif
    </form>
</body>
</html>
