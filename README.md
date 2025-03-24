🐥 SSIDuck
"SSIDuck happily waddles the wireless landscape—joyfully logging every Wi-Fi and Bluetooth device he finds. But beware: when he stops moving and new signals dry up, this little duck gets confused... and mischievous!"

🚀 What is SSIDuck?
Inspired by Psyduck’s delightful confusion, SSIDuck is your Raspberry Pi-powered wardriving companion designed to capture, log, and analyze Wi-Fi and Bluetooth networks. As you roam, SSIDuck uses Kismet and Bettercap to track wireless networks and Bluetooth devices, logging detailed information, including MAC addresses, signal strengths, GPS coordinates, and more into easy-to-manage CSV files.

✅ Current Features:
Real-time Wi-Fi Logging (via Kismet):
Continuously monitors Wi-Fi networks, capturing detailed information and accurately logging MAC addresses, signal strengths, channels, authentication modes, and GPS coordinates​kismet_logger.

Bluetooth Device Scanning (Bettercap):
Actively scans and logs nearby Bluetooth Low Energy (BLE) devices, tracking details like signal strength, vendor information, and time discovered​bettercap_logger.

Integrated Data Logging:
Merges Wi-Fi and Bluetooth data streams into comprehensive CSV logs, ready for easy analysis and upload to Wigle.net​data_logger.

Automated GPS Virtual Device Management:
Uses socat to manage a reliable GPS virtual device (via UDP), ensuring accurate and continuous location tagging for your logs​gps_launcher.

🚀 Upcoming Main Tasks:
Wigle Uploader:
Automatically upload collected CSV logs to Wigle.net and retrieve your badges for effortless tracking of your wardriving achievements.

Central Launch & Monitoring Script:
One master script to effortlessly launch and monitor SSIDuck’s modules, ensuring reliability and keeping track of active processes.

Fun Terminal User Interface (TUI):
A colorful, intuitive TUI that visualizes real-time network and Bluetooth data in a playful, SSIDuck-themed interface.

🌟 Planned Future Enhancements:
SSIDuck's Confused Mode ("Attack Mode"):
When SSIDuck detects you're stationary and no new networks or devices appear, he gets adorably confused and switches into a mischievous mode—actively probing and interacting with nearby Wi-Fi and Bluetooth networks.

Attack Automation:
Future scripts to automate wireless attacks, penetration testing, and active probing, triggered by SSIDuck’s boredom.

Interactive GUI & Virtual SSIDuck Pet:
Eventually, SSIDuck will evolve into a virtual pet living on your screen, expressing emotions, cracking jokes, and entertaining you as he logs or probes the wireless world around him!

🛠️ Additional Improvements:
Enhance logging detail and structure for seamless integration with the TUI and GUI.
Expand configurable settings through an easy-to-edit JSON file.
Thoroughly document and comment all scripts for clear, accessible code.
SSIDuck is more than just a wardriving tool—he's your quirky wireless companion, combining fun with serious network discovery. Happy waddling! 🦆✨



DISCLAIMERS AND LEGAL NOTICE

SSIDuck is provided strictly for educational, research, and lawful security auditing purposes.  
It is illegal to access, interfere with, or hack any computer network or system without explicit written permission from the rightful owner. Such actions are a criminal offense under Australian law (including the Cybercrime Act 2001 (Cth) and Telecommunications (Interception and Access) Act 1979 (Cth)) and international cybercrime laws.

Unauthorized access, interception of data, or network interference without consent is not only illegal but also unethical and strongly condemned by the author.  
The author(s), contributors, and maintainers of SSIDuck do not condone, support, or encourage any illegal, unethical, or malicious use of this software.

By using this tool, you agree that:  
- You will only use SSIDuck in environments where you have explicit authorization.  
- You understand that any misuse is solely your responsibility.  
- You are fully responsible for ensuring compliance with all laws and regulations in your jurisdiction.

This software is provided “as is,” without warranty of any kind, express or implied, including but not limited to warranties of merchantability, fitness for a particular purpose, or non-infringement.  
In no event shall the author(s), contributors, or maintainers be held liable for any damages or legal issues arising from the use, misuse, or distribution of this software.

**Do the right thing: messing with network networks without permission is illegal, unethical, DONT BE A QUACK.**



## License

SSIDuck is licensed under the [GNU General Public License v3.0](LICENSE).

Happy waddling! 🦆✨
![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)




