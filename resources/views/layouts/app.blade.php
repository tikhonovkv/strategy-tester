<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>@yield('title', 'Futures Funding — сверка модель → счёт')</title>
    <style>
        :root {
            --bg: #0b0f19;
            --card: #131a2a;
            --border: #222d45;
            --text: #e2e8f0;
            --muted: #94a3b8;
            --accent: #3b82f6;
            --green: #10b981;
            --red: #ef4444;
            --amber: #f59e0b;
        }
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {
            background: var(--bg);
            color: var(--text);
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            line-height: 1.5;
        }
        header {
            background: #0f1624;
            border-bottom: 1px solid var(--border);
            padding: 16px 24px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            flex-wrap: wrap;
            gap: 12px;
        }
        .brand {
            font-size: 18px;
            font-weight: 700;
            color: #fff;
            text-decoration: none;
        }
        .brand span { color: var(--accent); }
        nav { display: flex; gap: 16px; flex-wrap: wrap; }
        nav a {
            color: var(--muted);
            text-decoration: none;
            font-size: 14px;
            font-weight: 500;
            padding: 6px 10px;
            border-radius: 6px;
        }
        nav a:hover, nav a.active {
            color: #fff;
            background: rgba(59, 130, 246, 0.15);
        }
        .container {
            max-width: 1200px;
            margin: 0 auto;
            padding: 28px 20px 60px;
        }
        .grid-4 {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
            gap: 16px;
            margin-bottom: 24px;
        }
        .card {
            background: var(--card);
            border: 1px solid var(--border);
            border-radius: 10px;
            padding: 20px;
            margin-bottom: 20px;
        }
        .stat-label { font-size: 12px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.05em; }
        .stat-val { font-size: 28px; font-weight: 700; margin-top: 6px; }
        .pos { color: var(--green); }
        .neg { color: var(--red); }
        .warn { color: var(--amber); }
        h1 { font-size: 24px; margin-bottom: 8px; }
        h2 { font-size: 18px; margin-bottom: 14px; color: #fff; }
        p.sub { color: var(--muted); font-size: 14px; margin-bottom: 24px; }
        table {
            width: 100%;
            border-collapse: collapse;
            font-size: 14px;
        }
        th, td {
            text-align: left;
            padding: 10px 12px;
            border-bottom: 1px solid var(--border);
        }
        th { color: var(--muted); font-weight: 600; font-size: 12px; text-transform: uppercase; }
        .badge {
            display: inline-block;
            padding: 3px 8px;
            border-radius: 4px;
            font-size: 12px;
            font-weight: 600;
        }
        .badge-green { background: rgba(16, 185, 129, 0.15); color: var(--green); }
        .badge-red { background: rgba(239, 68, 68, 0.15); color: var(--red); }
        .badge-blue { background: rgba(59, 130, 246, 0.15); color: var(--accent); }
        .badge-amber { background: rgba(245, 158, 11, 0.15); color: var(--amber); }
        .btn {
            display: inline-block;
            background: var(--accent);
            color: #fff;
            padding: 8px 16px;
            border-radius: 6px;
            text-decoration: none;
            font-size: 14px;
            font-weight: 600;
            border: none;
            cursor: pointer;
        }
        .btn-green { background: var(--green); }
        .btn-outline {
            background: transparent;
            border: 1px solid var(--border);
            color: var(--text);
        }
        .btn:hover { opacity: 0.9; }
        input[type="text"], input[type="password"] {
            width: 100%;
            background: #0b101d;
            border: 1px solid var(--border);
            color: var(--text);
            padding: 9px 12px;
            border-radius: 6px;
            font-size: 14px;
        }
        .alert {
            background: rgba(16, 185, 129, 0.15);
            border: 1px solid var(--green);
            color: #d1fae5;
            padding: 12px 16px;
            border-radius: 8px;
            margin-bottom: 20px;
            font-size: 14px;
        }
        footer {
            text-align: center;
            color: var(--muted);
            font-size: 13px;
            padding: 24px;
            border-top: 1px solid var(--border);
        }
    </style>
</head>
<body>
    <header>
        <a href="{{ route('home') }}" class="brand">Futures<span>Funding</span></a>
    </header>
    <div class="container">
        @yield('content')
    </div>
    <footer>
        Futures Funding Bot &middot; funding + цена, сверка модель → счёт
    </footer>
</body>
</html>
