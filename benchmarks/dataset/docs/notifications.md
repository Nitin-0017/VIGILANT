# Notifications

## Email

`send_email` takes a recipient, a subject and a body.

## Webhooks

`send_webhook` posts an event to a customer's URL.

## Webhook delivery

If the receiving server is down, delivery is attempted again with growing delays.

## Daily summaries

Customers get one message each morning listing what happened the previous day.

## Quiet hours

No text messages are sent at night in the customer's time zone.
