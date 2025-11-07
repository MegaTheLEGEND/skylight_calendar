# skylight_calendar
Integrate skylight calendar into home assistant



# Install

- Put these files into the custom components folder in home assistant.
- Restart Home assistant. 
- setup skylight calendar in devices and services.

# Setup

- Log into https://ourskylight.com
- Open the calendar you wish to add. 
- The url contains your Frame ID. https://ourskylight.com/calendar/`XXXXXXX`

- to get your authentication code you need to open the browser dev tools `F12`
- go to the network tab and reload the page.
- you will see requests. you need to find the one with auth headers.
- you might try searching for the request with the word `start`. 
- what you are looking for will look like `Authorization: Basic XXXXXXXXXXXXXXXXXXXXXXXXXXXXXX`