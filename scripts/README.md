# Backend Scripts

Utility scripts for the Expert Avatar backend.

## generate_hedra_avatar.py

A standalone script to generate a Hedra avatar from an image file.

### Usage

1. **Set your image path** in the script:
   ```python
   IMAGE_PATH = "/path/to/your/image.jpg"
   ```

2. **Ensure your `.env` file has your Hedra API key**:
   ```bash
   HEDRA_API_KEY=your_key_here
   ```

3. **Run the script**:
   ```bash
   cd expert-avatar-backend
   python scripts/generate_hedra_avatar.py
   ```
   
   Or if using `uv`:
   ```bash
   cd expert-avatar-backend
   uv run python scripts/generate_hedra_avatar.py
   ```

4. **Copy the asset ID** from the output:
   ```
   ✅ SUCCESS!
   Your Hedra avatar asset ID is:
   
       abc123def456
   ```

### What it does

1. Loads `HEDRA_API_KEY` from your `.env` file
2. Creates a new Hedra asset
3. Uploads your image to that asset
4. Prints the asset ID for use in your avatar configuration

### Image Requirements

- Supported formats: `.jpg`, `.jpeg`, `.png`, `.gif`, `.webp`, `.bmp`
- The image should be a clear portrait photo for best results
- Hedra recommends centered, front-facing images

### Troubleshooting

- **Error: .env file not found**: Make sure you're running from the `expert-avatar-backend` directory and have a `.env` file
- **Error: HEDRA_API_KEY not found**: Add `HEDRA_API_KEY=your_key` to your `.env` file
- **Error: Image file not found**: Check that your `IMAGE_PATH` is correct and the file exists
- **API errors**: Check that your Hedra API key is valid and has sufficient credits

