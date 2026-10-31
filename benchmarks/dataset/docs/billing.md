# Billing

## Invoices

`Invoice.save` validates an invoice before writing it. Invoices are numbered in sequence.

## Refunds

Money goes back to the original card within five working days.

## Currency

Customers pay in their local currency, converted at the daily exchange rate.

## Taxes

Sales tax is added at checkout and rounded to the nearest cent.

## Payment retries

If a card is declined, we try again a few times over the following days before giving up.
