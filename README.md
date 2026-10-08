# some servers for Home Assistant

This is a repo of simple Home Assistant Apps for serving stuff.\
It has a simple minecraft java server, a simple nginx webserver,\
a Claude connector for mail (IMAP/SMTP) and calendar (CalDAV), see `claude-mail-mcp-app/README.md`,\
a plain-HTTP bridge for an old Nokia (J2ME) phone, see `nokia-bridge-app/README.md`,\
Signal for that phone (linked device, encrypted transport), see `nokia-signal-app/README.md`,\
and a daily morning briefing without AI (calendar, mails, deadlines), see `morgen-briefing-app/README.md`

For installation add this repository to your Home Assistant App repositories.

[![Open your Home Assistant instance and show the app store with a specific repository URL pre-filled.](https://my.home-assistant.io/badges/supervisor_store.svg)](https://my.home-assistant.io/redirect/supervisor_store/?repository_url=https%3A%2F%2Fgithub.com%2FLord3n20%2Fserver-homeassistant)

Then you can install it via the appstore.

<!--
Notes to developers after forking or using the github template feature:
- While developing comment out the 'image' key from 'example/config.yaml' to make the supervisor build the app locally.
  - Remember to put this back when pushing up your changes.
- When you merge to the 'main' branch of your repository a new build will be triggered.
  - Make sure you adjust the 'version' key in 'example/config.yaml' when you do that.
  - Make sure you update 'example/CHANGELOG.md' when you do that.
  - The first time this runs you might need to adjust the image configuration on github container registry to make it public.
  - You may also need to adjust the GitHub Actions configuration (Settings > Actions > General > Workflow > Read & Write).
- Update the repository check in '.github/workflows/build-app.yaml' to match your repository name
  (the 'github.repository' condition in the 'prepare' job).
- Adjust the 'image' key in 'example/config.yaml' so it points to your username instead of 'home-assistant'
  (e.g., 'ghcr.io/my-username/my-app').
- Rename the example directory.
  - The 'slug' key in 'example/config.yaml' should match the directory name.
- Adjust all keys/urls that point to 'home-assistant' to now point to your user/fork.
- Share your repository on the forums https://community.home-assistant.io/c/projects/9
- Do awesome stuff!
 -->
