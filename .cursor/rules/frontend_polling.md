# Frontend Polling and Chart Update Rules

## Chart Update Rules
- Redraw/reload any chart only if its data has really changed (compare data arrays, not just timestamps)
- Avoid unnecessary chart redraws to prevent flickering and performance issues

## Polling Schedule Rules

### Afternoon Polling (14:00-14:30) - FOR TOMORROW'S PRICES
- Every day, starting from 14:00 Helsinki time, start polling backend once per minute
- This polls specifically for TOMORROW's prices
- Continue frequent polling until TOMORROW's prices for the whole day are received
- Stop frequent polling once TOMORROW's full day prices are available

### Extended Afternoon Polling (14:30-15:00) - FOR TOMORROW'S PRICES
- If TOMORROW's prices haven't been received by 14:30, change polling frequency to 5 minutes
- This polls specifically for TOMORROW's prices
- Continue 5-minute polling until:
  - TOMORROW's prices are available, OR
  - Time reaches 15:00

### Evening/Night Polling (15:00-24:00)
- Between 15:00-24:00, poll backend every 1 hour IF tomorrow's prices haven't been received yet
- Stop polling once tomorrow's prices are available

### Morning Polling (0:00-14:00)
- Between 0:00-14:00, poll backend every 1 hour IF today's prices haven't been received yet
- Stop polling once today's prices are available

### Midnight Behavior
- Leave the midnight behavior as is (it's working well)
- Midnight transition should continue to refresh both charts
- At midnight, tomorrow's data becomes today's data, so redraw IS needed
- The data change detection correctly handles this: when date changes, existingData will be null, so charts will redraw

## Data Change Detection
- Before redrawing a chart, compare the new data with existing data
- Only redraw if:
  - Data array length changed, OR
  - Any price values changed, OR
  - Date changed
- Use deep comparison of data arrays to detect actual changes
