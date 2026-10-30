def send_reminders(users, mailer, today):
    sent = 0
    for user in users:
        if user.email is None:
            continue
        if user.unsubscribed:
            continue
        if user.last_reminder == today:
            continue
        mailer.send(user.email, "Your weekly summary")
        user.last_reminder = today
        sent += 1
    return sent
